#!/usr/bin/env python3
"""Read-only GitHub PR evidence inspector. NEVER authorizes or performs a merge.

Usage (advisory, caller-declared policy):
  python3 scripts/inspect-pr-review-evidence.py \
    --repo OWNER/REPO --pr 123 --base main --expected-head FULL_40_CHAR_SHA \
    --required-check 'Index Integrity' --min-approvals 1

Reads a PR twice, reviews, check-runs, and legacy commit statuses via gh api GET.
Does not inspect effective branch protection or rulesets. Exit 0 means the
*declared evidence* passed, NOT permission to merge. Owner/repository controls
must separately enforce branch protection, required CI, and acceptance.
"""
import argparse
import json
import os
import re
import subprocess
import sys

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", re.ASCII)
REF_RE = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$", re.ASCII)
SHA_RE = re.compile(r"^[a-fA-F0-9]{40}$", re.ASCII)


class ReadError(Exception):
    """Read-only GitHub API transport or decoding error."""


def fetch_gh(path):
    """Execute exactly one authenticated GET, never gh pr, POST, PUT, or PATCH."""
    if not path.startswith("repos/") or not re.fullmatch(r"[A-Za-z0-9_./?=&-]+", path):
        raise ReadError("INVALID_READ_PATH")
    env = dict(os.environ, GH_PAGER="cat", GH_PROMPT_DISABLED="1")
    env.pop("GH_HOST", None)
    env.pop("GH_ENTERPRISE_TOKEN", None)
    try:
        result = subprocess.run(
            ["gh", "api", "--hostname", "github.com", "--method", "GET", path], capture_output=True,
            text=True, env=env, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReadError("GET_UNAVAILABLE") from exc
    if result.returncode != 0:
        # Deliberately omit stdout/stderr: they may contain private metadata.
        raise ReadError("GET_DENIED_OR_FAILED")
    try:
        return json.loads(result.stdout)
    except (ValueError, UnicodeError) as exc:
        raise ReadError("INVALID_JSON") from exc


def evaluate(first, last, reviews, checks, statuses, files, *,
             expected_sha, expected_base, expected_repo, expected_number,
             required_checks, min_approvals):
    """Classify evidence only; even a pass is not a GitHub merge authorization."""
    issues = []
    if not required_checks or min_approvals < 1:
        issues.append("DECLARED_POLICY_MISSING")

    if not isinstance(first, dict) or not isinstance(last, dict):
        raise ReadError("INVALID_PR_RESULT")

    head = first.get("head") or {}
    base = first.get("base") or {}
    latest_head = last.get("head") or {}
    latest_base = last.get("base") or {}
    sha = head.get("sha")
    if sha != expected_sha:
        issues.append("UNEXPECTED_HEAD_SHA")
    if latest_head.get("sha") != sha or latest_head.get("sha") != expected_sha:
        issues.append("CHANGED_PR_HEAD")
    if base.get("ref") != expected_base or latest_base.get("ref") != expected_base:
        issues.append("UNEXPECTED_BASE_BRANCH")
    if base.get("sha") != latest_base.get("sha"):
        issues.append("CHANGED_BASE_REVISION")
    for p in (first, last):
        if (p.get("base") or {}).get("repo", {}).get("full_name") != expected_repo:
            issues.append("UNEXPECTED_REPOSITORY")
        if p.get("html_url") != f"https://github.com/{expected_repo}/pull/{expected_number}":
            issues.append("UNEXPECTED_PR_URL")
        if p.get("number") != expected_number:
            issues.append("UNEXPECTED_PR_NUMBER")
    if (not isinstance(files, list) or len(files) >= 100
            or first.get("changed_files") != len(files)
            or last.get("changed_files") != len(files)):
        issues.append("INCOMPLETE_CHANGED_FILES")
        files = []
    if any(isinstance(item, dict)
           and str(item.get("filename", "")).startswith(".github/workflows/")
           for item in files):
        issues.append("WORKFLOW_CHANGED_REQUIRES_OWNER_REVIEW")
    if first.get("state") != "open" or last.get("state") != "open":
        issues.append("PR_NOT_OPEN")
    if first.get("draft") or last.get("draft"):
        issues.append("DRAFT_PR")
    if first.get("mergeable") is not True or last.get("mergeable") is not True:
        issues.append("MERGEABILITY_NOT_CONFIRMED")

    if not isinstance(reviews, list) or len(reviews) >= 100:
        issues.append("INCOMPLETE_REVIEWS")
        reviews = []
    review_by_user = {}
    for review in reviews:
        if not isinstance(review, dict):
            continue
        user = review.get("user") or {}
        user_id = user.get("id")
        if isinstance(user_id, int):
            review_by_user[user_id] = review

    author_id = (first.get("user") or {}).get("id")
    accepted_reviewers = [
        user_id for user_id, v in review_by_user.items()
        if user_id != author_id
        and (v.get("user") or {}).get("type") != "Bot"
        and not str((v.get("user") or {}).get("login", "")).endswith("[bot]")
        and v.get("author_association") in ("OWNER", "MEMBER", "COLLABORATOR")
        and v.get("state") == "APPROVED" and v.get("commit_id") == expected_sha
    ]
    if len(accepted_reviewers) < min_approvals:
        issues.append("INSUFFICIENT_CURRENT_APPROVALS")

    if not isinstance(checks, dict) or not isinstance(statuses, dict):
        raise ReadError("INVALID_CHECK_RESULT")
    runs = checks.get("check_runs")
    total = checks.get("total_count")
    if not isinstance(runs, list) or not isinstance(total, int) or total != len(runs) or total >= 100:
        issues.append("INCOMPLETE_CHECK_RESULTS")
        runs = runs if isinstance(runs, list) else []
    legacy = statuses.get("statuses")
    if (not isinstance(legacy, list) or len(legacy) >= 100
            or statuses.get("total_count") != len(legacy)):
        issues.append("INCOMPLETE_LEGACY_STATUSES")
        legacy = legacy if isinstance(legacy, list) else []
    if statuses.get("sha") != expected_sha:
        issues.append("STALE_COMMIT_STATUS")

    for name in required_checks:
        found = (
            [("run", x) for x in runs if isinstance(x, dict) and x.get("name") == name]
            + [("status", x) for x in legacy if isinstance(x, dict) and x.get("context") == name]
        )
        if not found:
            issues.append("MISSING_REQUIRED_CHECK:" + name)
        elif len(found) != 1:
            issues.append("AMBIGUOUS_REQUIRED_CHECK:" + name)
        else:
            source, evidence = found[0]
            if source == "run" and evidence.get("head_sha") != expected_sha:
                issues.append("STALE_CHECK_RECEIPT:" + name)
            if source == "run":
                app = evidence.get("app") or {}
                if app.get("slug") != "github-actions":
                    issues.append("UNTRUSTED_CHECK_PROVIDER:" + name)
                passing = evidence.get("status") == "completed" and evidence.get("conclusion") == "success"
            else:
                # Caller-supplied check names cannot authenticate legacy Status publishers.
                # Only GitHub Actions check-runs are supported as positive evidence here.
                issues.append("UNVERIFIED_LEGACY_CHECK:" + name)
                passing = evidence.get("state") == "success"
            if not passing:
                issues.append("REQUIRED_CHECK_NOT_SUCCESS:" + name)
    issues = sorted(set(issues))
    return {
        "verdict": "BLOCKED" if issues else "EVIDENCE_PASS_NOT_MERGE_AUTHORIZATION",
        "issues": issues,
        "current_head": sha,
        "current_base": base.get("ref"),
        "current_base_sha": base.get("sha"),
        "approval_count_on_current_head": len(accepted_reviewers),
        "required_checks_declared": list(required_checks),
        "policy_source": "CALLER_DECLARED_NOT_EFFECTIVE_GITHUB_RULES",
        "effective_branch_policy_verified": False,
        "merge_authorized": False,
        "read_only": True,
    }


def inspect_live(repo, number, expected_sha, expected_base,
                 required_checks, min_approvals, fetch=fetch_gh):
    """Gather five independent read-only API snapshots; second PR read detects drift."""
    if (not REPO_RE.fullmatch(repo) or any(part in (".", "..") for part in repo.split("/"))
            or not SHA_RE.fullmatch(expected_sha) or not REF_RE.fullmatch(expected_base)):
        return {"verdict": "UNKNOWN", "issues": ["INVALID_INPUT"], "merge_authorized": False}
    if not isinstance(number, int) or number < 1:
        return {"verdict": "UNKNOWN", "issues": ["INVALID_INPUT"], "merge_authorized": False}
    if not required_checks or min_approvals < 1:
        return {"verdict": "BLOCKED", "issues": ["DECLARED_POLICY_MISSING"], "merge_authorized": False}

    base = f"repos/{repo}"
    try:
        first = fetch(f"{base}/pulls/{number}")
        head = (first.get("head") or {}).get("sha")
        # A changed PR is already invalid, but keep a stable, small read-only
        # workflow for an independent final snapshot.
        if not isinstance(head, str) or not SHA_RE.fullmatch(head):
            raise ReadError("MISSING_HEAD")
        review_data = fetch(f"{base}/pulls/{number}/reviews?per_page=100")
        check_data = fetch(f"{base}/commits/{head}/check-runs?per_page=100")
        status_data = fetch(f"{base}/commits/{head}/status?per_page=100")
        file_data = fetch(f"{base}/pulls/{number}/files?per_page=100")
        last = fetch(f"{base}/pulls/{number}")
        return evaluate(first, last, review_data, check_data, status_data, file_data,
                        expected_sha=expected_sha, expected_base=expected_base,
                        expected_repo=repo, expected_number=number,
                        required_checks=required_checks, min_approvals=min_approvals)
    except (ReadError, KeyError, ValueError, TypeError, AttributeError):
        return {
            "verdict": "UNKNOWN",
            "issues": ["FETCH_FAILED"],
            "merge_authorized": False,
            "effective_branch_policy_verified": False,
            "read_only": True,
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="GitHub owner/repository")
    parser.add_argument("--pr", required=True, type=int, help="Existing PR number")
    parser.add_argument("--expected-head", required=True, help="Expected full 40-char head SHA")
    parser.add_argument("--base", required=True, help="Expected base branch name")
    parser.add_argument("--required-check", action="append", default=[], help="Caller-declared check; repeat per check")
    parser.add_argument("--min-approvals", type=int, default=0, help="Caller-declared minimum approval count")
    args = parser.parse_args(argv)
    evidence = inspect_live(args.repo, args.pr, args.expected_head, args.base,
                            args.required_check, args.min_approvals)
    print(json.dumps(evidence, sort_keys=True))
    if evidence["verdict"] == "UNKNOWN":
        return 3
    if evidence["verdict"] == "BLOCKED":
        return 2
    return 0  # Advisory evidence only. NEVER authorization to merge.


if __name__ == "__main__":
    sys.exit(main())