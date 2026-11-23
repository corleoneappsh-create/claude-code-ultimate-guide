#!/usr/bin/env python3
"""Unit tests for a read-only GitHub PR evidence inspector (no network)."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).with_name("inspect-pr-review-evidence.py")
SPEC = importlib.util.spec_from_file_location("inspect_pr_review_evidence", SOURCE)
inspector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inspector)

SHA = "a" * 40
BASE_SHA = "b" * 40
REPO = "FlorianBruniaux/claude-code-ultimate-guide"


def pr(sha=SHA, base_sha=BASE_SHA, base="main", draft=False, state="open"):
    return {"number": 110, "state": state, "draft": draft,
            "mergeable": True, "head": {"sha": sha},
            "base": {"ref": base, "sha": base_sha, "repo": {"full_name": REPO}},
            "html_url": "https://github.com/" + REPO + "/pull/110",
            "changed_files": 1,
            "user": {"login": "original-author", "id": 1}}


def reviews(commit=SHA, state="APPROVED"):
    return [{"user": {"login": "human-reviewer", "type": "User", "id": 2},
             "author_association": "COLLABORATOR",
             "state": state, "commit_id": commit, "submitted_at": "2026-11-22T18:00:00Z"}]


def checks(sha=SHA, status="completed", conclusion="success"):
    return {"total_count": 1,
            "check_runs": [{"name": "Index Integrity", "head_sha": sha,
                            "status": status, "conclusion": conclusion,
                            "app": {"slug": "github-actions"}}]}


def statuses(sha=SHA):
    return {"sha": sha, "total_count": 0, "statuses": []}


def audit(first=None, last=None, rs=None, cs=None, ss=None,
          fs=None, expected=SHA, base="main", required=("Index Integrity",), minimum=1):
    return inspector.evaluate(
        first or pr(), last or pr(),
        reviews() if rs is None else rs, checks() if cs is None else cs,
        statuses() if ss is None else ss,
        [{"filename": "guide/overview.md"}] if fs is None else fs,
        expected_sha=expected, expected_base=base,
        expected_repo=REPO, expected_number=110,
        required_checks=required, min_approvals=minimum)


class InspectorTests(unittest.TestCase):
    def test_nominal_is_only_evidence_not_merge_authorization(self):
        data = audit()
        self.assertEqual(data["verdict"], "EVIDENCE_PASS_NOT_MERGE_AUTHORIZATION")
        self.assertIs(data["merge_authorized"], False)
        self.assertIs(data["effective_branch_policy_verified"], False)
        self.assertEqual(data["issues"], [])

    def test_missing_review_is_blocked(self):
        data = audit(rs=[])
        self.assertEqual(data["verdict"], "BLOCKED")
        self.assertIn("INSUFFICIENT_CURRENT_APPROVALS", data["issues"])

    def test_approval_on_stale_commit_is_not_accepted(self):
        self.assertIn("INSUFFICIENT_CURRENT_APPROVALS",
                      audit(rs=reviews(commit="c" * 40))["issues"])

    def test_changes_requested_overrides_previous_approval(self):
        changed = reviews() + [{"user": {"login": "human-reviewer", "type": "User", "id": 2},
                                "author_association": "COLLABORATOR",
                                "state": "CHANGES_REQUESTED", "commit_id": SHA,
                                "submitted_at": "2026-11-22T19:00:00Z"}]
        self.assertIn("INSUFFICIENT_CURRENT_APPROVALS", audit(rs=changed)["issues"])

    def test_pending_check_is_blocked(self):
        self.assertIn("REQUIRED_CHECK_NOT_SUCCESS:Index Integrity",
                      audit(cs=checks(status="in_progress", conclusion=None))["issues"])

    def test_failure_check_is_blocked(self):
        self.assertIn("REQUIRED_CHECK_NOT_SUCCESS:Index Integrity",
                      audit(cs=checks(conclusion="failure"))["issues"])

    def test_missing_check_is_blocked(self):
        self.assertIn("MISSING_REQUIRED_CHECK:Index Integrity",
                      audit(cs={"total_count": 0, "check_runs": []})["issues"])

    def test_check_stale_sha_is_blocked(self):
        self.assertIn("STALE_CHECK_RECEIPT:Index Integrity",
                      audit(cs=checks(sha="c" * 40))["issues"])

    def test_changed_pr_head_is_blocked(self):
        self.assertIn("CHANGED_PR_HEAD", audit(last=pr(sha="c" * 40))["issues"])

    def test_expected_sha_mismatch_is_blocked(self):
        self.assertIn("UNEXPECTED_HEAD_SHA", audit(expected="c" * 40)["issues"])

    def test_base_changes_during_audit_is_blocked(self):
        self.assertIn("CHANGED_BASE_REVISION", audit(last=pr(base_sha="c" * 40))["issues"])

    def test_wrong_base_branch_is_blocked(self):
        self.assertIn("UNEXPECTED_BASE_BRANCH", audit(first=pr(base="develop"))["issues"])

    def test_draft_pr_is_blocked(self):
        self.assertIn("DRAFT_PR", audit(first=pr(draft=True))["issues"])

    def test_unmergeable_or_unknown_is_blocked(self):
        for value in (False, None):
            with self.subTest(value=value):
                p = pr()
                p["mergeable"] = value
                self.assertIn("MERGEABILITY_NOT_CONFIRMED", audit(first=p)["issues"])

    def test_ambiguous_duplicate_checks_are_not_success(self):
        cs = checks()
        cs["total_count"] = 2
        cs["check_runs"].append(dict(cs["check_runs"][0]))
        self.assertIn("AMBIGUOUS_REQUIRED_CHECK:Index Integrity", audit(cs=cs)["issues"])

    def test_incomplete_check_page_is_blocked(self):
        cs = checks()
        cs["total_count"] = 2
        self.assertIn("INCOMPLETE_CHECK_RESULTS", audit(cs=cs)["issues"])

    def test_missing_owner_declared_policy_is_blocked(self):
        for required, approvals in (((), 1), (("Index Integrity",), 0)):
            with self.subTest(required=required, approvals=approvals):
                self.assertIn("DECLARED_POLICY_MISSING", audit(required=required, minimum=approvals)["issues"])

    def test_read_only_gets_latest_pr_twice_and_never_merges(self):
        requested = []
        def fetch(path):
            requested.append(path)
            if path.endswith("/reviews?per_page=100"):
                return reviews()
            if path.endswith("/check-runs?per_page=100"):
                return checks()
            if path.endswith("/status?per_page=100"):
                return statuses()
            if path.endswith("/files?per_page=100"):
                return [{"filename": "guide/overview.md"}]
            if path.endswith("/pulls/110"):
                return pr()
            raise AssertionError(path)
        data = inspector.inspect_live(REPO, 110, SHA, "main", ("Index Integrity",), 1, fetch)
        self.assertEqual(data["verdict"], "EVIDENCE_PASS_NOT_MERGE_AUTHORIZATION")
        self.assertEqual(requested.count("repos/" + REPO + "/pulls/110"), 2)
        self.assertEqual(len(requested), 6)
        self.assertTrue(all("/merge" not in x and not x.endswith("/reviews") for x in requested))

    def test_repository_path_traversal_is_rejected_before_requests(self):
        def never_fetch(_):
            self.fail("invalid GitHub repo must not make API requests")
        for invalid in ("../..", "./project", "org/..", "org/.", "a//b"):
            with self.subTest(repo=invalid):
                data = inspector.inspect_live(invalid, 110, SHA, "main",
                                              ("Index Integrity",), 1, never_fetch)
                self.assertEqual(data["verdict"], "UNKNOWN")

    def test_gh_transport_uses_get_only(self):
        import subprocess
        completed = subprocess.CompletedProcess(args=["gh"], returncode=0, stdout='{"ok": true}', stderr="")
        with patch.object(inspector.subprocess, "run", return_value=completed) as runner:
            obj = inspector.fetch_gh("repos/" + REPO + "/pulls/110")
        self.assertEqual(obj, {"ok": True})
        self.assertEqual(runner.call_args.args[0],
                         ["gh", "api", "--hostname", "github.com",
                          "--method", "GET", "repos/" + REPO + "/pulls/110"])

    def test_drive_by_review_not_counted(self):
        for association in ("NONE", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", None):
            with self.subTest(association=association):
                candidate = reviews()
                candidate[0]["author_association"] = association
                self.assertIn("INSUFFICIENT_CURRENT_APPROVALS",
                              audit(rs=candidate)["issues"])

    def test_same_user_id_as_author_not_counted_even_if_login_differs(self):
        candidate = reviews()
        candidate[0]["user"]["id"] = 1
        candidate[0]["user"]["login"] = "renamed-author"
        self.assertIn("INSUFFICIENT_CURRENT_APPROVALS", audit(rs=candidate)["issues"])

    def test_check_from_untrusted_github_app_is_not_counted(self):
        candidate = checks()
        candidate["check_runs"][0]["app"] = {"slug": "some-other-app"}
        self.assertIn("UNTRUSTED_CHECK_PROVIDER:Index Integrity", audit(cs=candidate)["issues"])

    def test_workflow_file_change_requires_manual_review(self):
        self.assertIn("WORKFLOW_CHANGED_REQUIRES_OWNER_REVIEW",
                      audit(fs=[{"filename": ".github/workflows/index-integrity.yml"}])["issues"])

    def test_missing_pr_file_page_is_blocked(self):
        self.assertIn("INCOMPLETE_CHANGED_FILES", audit(fs=[])["issues"])

    def test_full_reviews_page_is_incomplete(self):
        self.assertIn("INCOMPLETE_REVIEWS", audit(rs=reviews() * 100)["issues"])

    def test_legacy_only_status_cannot_prove_trusted_ci_app(self):
        candidate = {"sha": SHA, "total_count": 1,
                     "statuses": [{"context": "Index Integrity", "state": "success"}]}
        empty = {"total_count": 0, "check_runs": []}
        self.assertIn("UNVERIFIED_LEGACY_CHECK:Index Integrity",
                      audit(cs=empty, ss=candidate)["issues"])

    def test_incomplete_status_page_is_blocked(self):
        candidate = statuses()
        candidate["total_count"] = 2
        self.assertIn("INCOMPLETE_LEGACY_STATUSES", audit(ss=candidate)["issues"])

    def test_wrong_base_repository_or_web_origin_is_blocked(self):
        other = pr()
        other["base"]["repo"]["full_name"] = "unrelated/project"
        self.assertIn("UNEXPECTED_REPOSITORY", audit(first=other)["issues"])
        other = pr()
        other["html_url"] = "https://example.org/forge/pull/110"
        self.assertIn("UNEXPECTED_PR_URL", audit(first=other)["issues"])

    def test_api_error_returns_unknown_not_ready(self):
        def fetch(path):
            raise inspector.ReadError("ACCESS_DENIED")
        data = inspector.inspect_live(REPO, 110, SHA, "main", ("Index Integrity",), 1, fetch)
        self.assertEqual(data["verdict"], "UNKNOWN")
        self.assertIs(data["merge_authorized"], False)
        self.assertIn("FETCH_FAILED", data["issues"])


if __name__ == "__main__":
    unittest.main()
