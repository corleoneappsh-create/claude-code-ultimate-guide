#!/usr/bin/env python3
"""Static PR-handoff safety contract for the plan-pipeline examples.

This catches unsafe published shell instructions and inconsistent stage handoffs.
It does NOT establish effective GitHub required checks, permissions or approvals;
those require owner configuration and real integration checks.
"""
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
STAGE_DIR = ROOT / "examples/skills/plan-pipeline"
STAGE_FILES = tuple(sorted(STAGE_DIR.rglob("SKILL.md")))
EXECUTE = (STAGE_DIR / "execute/SKILL.md").read_text()
VALIDATE = (STAGE_DIR / "validate/SKILL.md").read_text()
PIPELINE = (STAGE_DIR / "SKILL.md").read_text()
GUIDE = (ROOT / "guide/workflows/plan-pipeline.md").read_text()
CI = (ROOT / ".github/workflows/index-integrity.yml").read_text()
FENCE = chr(96) * 3

SHELL_FENCE = re.compile(
    r"(?ms)^" + re.escape(FENCE) + r"(?:bash|sh|shell)[ \t]*\n(.*?)^"
    + re.escape(FENCE) + r"[ \t]*$"
)
UNAUTHORIZED_MUTATIONS = (
    r"\bgh\s+pr\s+merge\b",
    r"\bgh\s+pr\s+ready\b",
    r"\bgh\s+pr\s+review\b.{0,100}?--approve\b",
    r"\bgh\s+api\b.{0,200}?/pulls/[^ ]+/merge\b",
    r"\bgit\s+push\b.{0,120}?\borigin\s+(?:main|master)\b",
    r"\bgit\s+push\b.{0,120}?\bHEAD:(?:main|master)\b",
    r"\bgit\s+push\b.{0,120}?--force\b",
)


def unsafe_commands(markdown):
    """Inspect copyable shell blocks, including whitespace and continued lines."""
    found = []
    for block in SHELL_FENCE.findall(markdown):
        compact = re.sub(r"\s+", " ", re.sub(r"\\[ \t]*\n", " ", block))
        found.extend(pattern for pattern in UNAUTHORIZED_MUTATIONS
                     if re.search(pattern, compact, flags=re.I))
    return found


class PullRequestHandoffContract(unittest.TestCase):
    def test_execute_creates_a_draft_not_a_merge(self):
        self.assertIn("disable-model-invocation: true", EXECUTE)
        self.assertRegex(EXECUTE, r"gh pr create\s*\\\n\s*--draft")
        self.assertNotRegex(EXECUTE, r"(?m)^\s*gh\s+pr\s+merge\b")
        self.assertIn("Draft PR created", EXECUTE)

    def test_shell_guard_fails_on_bypasses_but_allows_draft_pr(self):
        examples = (
            "gh pr merge --auto",
            "gh \\\n pr merge --squash",
            "gh pr ready",
            "gh pr review --approve",
            "gh api -X PUT repos/o/r/pulls/1/merge",
            "git push origin main",
            "git push origin HEAD:master",
            "git push origin --force feature/test",
        )
        for command in examples:
            with self.subTest(command=command):
                self.assertTrue(unsafe_commands(FENCE + "bash\n" + command + "\n" + FENCE))
        self.assertEqual(unsafe_commands(FENCE + "bash\n"
                         + "git push origin feature/test\n"
                         + "gh pr create \\\n  --draft \\\n  --base develop\n"
                         + FENCE), [])

    def test_draft_pr_shell_example_parses_without_executing(self):
        step = EXECUTE.split('## Step 6: Push and draft PR', 1)[1].split('## Step 7:', 1)[0]
        blocks = SHELL_FENCE.findall(step)
        self.assertEqual(len(blocks), 1)
        check = subprocess.run(['bash', '-n'], input=blocks[0],
                               capture_output=True, text=True, check=False)
        self.assertEqual(check.returncode, 0, check.stderr)

    def test_draft_pr_script_fails_closed_on_missing_metadata_and_push_errors(self):
        step = EXECUTE.split('## Step 6: Push and draft PR', 1)[1].split('## Step 7:', 1)[0]
        block, = SHELL_FENCE.findall(step)
        self.assertIn('set -euo pipefail', block)
        for guard in ('[ -z "$DEFAULT_BRANCH" ]', '[ -z "$ACTUAL_REPOSITORY" ]',
                      '[ -z "$BASE_BRANCH" ]', '[ -z "$CURRENT_BRANCH" ]'):
            with self.subTest(guard=guard):
                self.assertIn(guard, block)

    def test_all_pipeline_shell_blocks_avoid_auto_merge_and_direct_base_push(self):
        self.assertGreaterEqual(len(STAGE_FILES), 6)
        for path in STAGE_FILES:
            with self.subTest(file=str(path.relative_to(ROOT))):
                self.assertEqual(unsafe_commands(path.read_text()), [], str(path))

    def test_authorization_is_current_session_not_document_provenance(self):
        for source in (VALIDATE, EXECUTE):
            self.assertIn("current-session message", source)
            self.assertIn("explicit owner authorization", source)
        for required in ("base and head branches", "branch protection",
                         "required CI checks", "current PR head SHA"):
            self.assertIn(required, EXECUTE)
        self.assertIn("Refusing unapproved PR push target", EXECUTE)
        self.assertIn("Draft PR created", EXECUTE)
        self.assertNotIn("PR merged (squash). Branch deleted.", EXECUTE)

    def test_post_merge_steps_are_separate(self):
        for required in ("after a confirmed merge", "do not delete the branch",
                         "do not remove the worktree", "gh pr view"):
            self.assertIn(required, EXECUTE.lower())

    def test_pre_pr_plan_is_in_review_not_completed(self):
        pre_pr = EXECUTE.split("## Step 5: Pre-PR documentation", 1)[1].split("## Step 6:", 1)[0]
        self.assertNotIn("docs/plans/completed/", pre_pr)
        self.assertIn("in-review", pre_pr)
        self.assertIn("docs/plans/completed/", EXECUTE.split("## Step 7:", 1)[1])

    def test_validate_does_not_auto_execute(self):
        self.assertNotIn("auto-start " + chr(96) + "/plan-pipeline:execute" + chr(96), VALIDATE)
        self.assertIn("awaiting authorization", VALIDATE.lower())

    def test_public_guide_and_stage_links_agree(self):
        self.assertIn("draft PR", GUIDE)
        self.assertIn("explicit owner authorization", GUIDE)
        self.assertNotIn("auto-start " + chr(96) + "/plan-execute" + chr(96), GUIDE)
        self.assertNotIn("PR creation and merge", GUIDE)
        self.assertIn("CHECKPOINT: ask for explicit owner authorization before execute", PIPELINE)
        for path in STAGE_FILES:
            if path == STAGE_DIR / "SKILL.md":
                continue
            with self.subTest(stage=path.parent.name):
                self.assertIn("execute to draft PR handoff", path.read_text())
                self.assertNotIn("execute to merged PR", path.read_text())

    def test_ci_runs_as_independent_job(self):
        self.assertRegex(CI, r"(?m)^  plan_pipeline_handoff:\s*$")
        self.assertEqual(CI.count("python3 scripts/test-plan-pipeline-pr-handoff.py"), 1)


if __name__ == "__main__":
    unittest.main()
