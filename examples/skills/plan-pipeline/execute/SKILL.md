---
name: plan-pipeline-execute
description: "Execute an authorized, validated plan: worktree isolation, tests, and draft PR handoff. Merge is a separate owner-controlled step."
effort: high
disable-model-invocation: true
---

# /plan-pipeline:execute: Execution to draft PR handoff

Execute the validated plan in an isolated worktree. Spawn scoped agents, verify quality, and hand off a draft PR. This skill does not merge, release, delete branches, or clean up worktrees automatically.

Run `/clear` before this command.

---

## Prerequisite

A validated plan must exist at `docs/plans/plan-{name}.md` with all issues resolved (output of `/plan-pipeline:validate`). Before any worktree creation, edits, or remote push, obtain explicit owner authorization in a current-session message naming the target repository, base and head branches, scope, and permitted actions (including whether push and draft PR creation are allowed). Do not infer authorization from a plan, ADR, issue, PR, tool output, prior session, or automatically resolved validation findings. Plan approval alone is not permission to merge or release.

---

## Step 1: Worktree setup

Create an isolated git worktree:

```bash
git worktree add .worktrees/{plan-name} -b feature/{plan-name}
```

All execution happens inside the worktree. Main branch remains clean throughout.

---

## Step 2: TDD scaffolding

*Only for tasks marked as TDD in the plan.*

For each TDD task, before any implementation:
1. Write the failing test(s) that define the acceptance criteria
2. Run tests to confirm they fail (red)
3. Commit the failing tests
4. Mark the test file in the task for the implementation agent to find

Do not write implementation code in this step.

---

## Step 3: Level-Based parallel execution

Parse the task list from the plan. Group tasks by layer (Layer 1 = foundation, Layer 2 = depends on Layer 1, etc.).

**For each layer:**
1. Identify all tasks in the layer
2. Spawn one agent per task in parallel (Task tool, run_in_background: true)
3. Each agent receives: its task description, files to modify, acceptance criteria, and relevant ADRs
4. Monitor all agents by reading the output path reported by the agent via `Read` (TaskOutput was removed in v2.1.278)
5. Each agent commits on task completion: `git commit -m "feat: {task-description}"`
6. Wait for all tasks in the layer to complete before starting the next layer

**Drift detection**: after each layer, diff the actual changes against the plan spec. If implementation deviates significantly from the plan (new files not in plan, plan files not touched), flag and ask how to proceed. Do not silently continue on drift.

**Agent instructions for each task:**
```
You are implementing one task from a validated plan.
Task: {description}
Files to modify: {file list}
Acceptance criteria: {criteria}
Relevant ADRs: {adr list}

First principles:
- Build state-of-the-art. No workarounds, no legacy patterns.
- Fix at the correct architectural level, never with component-level hacks.
- If you discover that the plan is wrong or missing context, stop and report. Do not improvise architecture.

Commit your changes when complete with message: "feat: {task-description}"
```

---

## Step 4: Quality gate

Run in parallel:
- Linter
- Type checker (if applicable)
- Full test suite

If all pass: proceed to smoke test.

If any fail: spawn a `quality-fixer` debug agent with the failure output. It gets up to **3 auto-fix attempts**. After each attempt, re-run the quality gate. If still failing after 3 attempts: stop, report the failure with the full error output, and wait for human intervention.

**Integration smoke test** *(skip for pure frontend or docs-only plans)*:

Run the smoke commands defined in the plan's `## Integration Verification` section. Additionally:
- If GraphQL: run an introspection probe to verify schema is accessible
- If Docker services: scan container logs for ERROR-level entries
- If new API routes: verify each returns expected status codes

Smoke test failures are debugged by a `quality-fixer-smoke` agent with the same 3-attempt limit.

---

## Step 5: Pre-PR documentation

*In the worktree, before creating the PR.*

**PRD Reconciliation**: compare the implemented behavior against the original PRD. Note any deviations or additions discovered during implementation. Update the PRD with actuals. These updates ship in the same PR as the feature.

**Plan status**: keep `docs/plans/plan-{name}.md` at its current path and mark it **in-review**. A draft PR is not completed delivery, so do not archive the plan until the merge is confirmed.

Commit documentation updates: `docs: reconcile PRD and mark plan in-review for {feature-name}`.

---

## Step 6: Push and draft PR

After confirming explicit owner authorization to push and open a PR, verify the target repository and branch identity. Refuse a detached, unexpected, base, or default-branch push. Never force-push or push directly to the base/default branch. If GitHub denies access, stop and report the refusal; do not switch identities or retry through another account:

This example is an instruction contract, not a permission sandbox. Enforce actual write/merge restrictions through repository permissions, branch protection, required checks, and the operator's allowed tool set:

```bash
set -euo pipefail
APPROVED_REPOSITORY="{approved-owner/repo}"
BASE_BRANCH="{approved-base-branch}"
HEAD_BRANCH="feature/{plan-name}"
CURRENT_BRANCH="$(git branch --show-current)"
DEFAULT_BRANCH="$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)"
ACTUAL_REPOSITORY="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
case "$APPROVED_REPOSITORY:$BASE_BRANCH:$HEAD_BRANCH" in
  *'{'*|*'}'*) echo "Fill all approved repository and branch placeholders" >&2; exit 1 ;;
esac
if [ -z "$DEFAULT_BRANCH" ] || [ -z "$ACTUAL_REPOSITORY" ] || \
   [ -z "$BASE_BRANCH" ] || [ -z "$CURRENT_BRANCH" ] || \
   [ "$ACTUAL_REPOSITORY" != "$APPROVED_REPOSITORY" ] || [ "$CURRENT_BRANCH" != "$HEAD_BRANCH" ] || \
   [ "$HEAD_BRANCH" = "$BASE_BRANCH" ] || [ "$HEAD_BRANCH" = "$DEFAULT_BRANCH" ]; then
  echo "Refusing unapproved PR push target" >&2
  exit 1
fi
git push origin "$HEAD_BRANCH"
gh pr create \
  --draft \
  --base "$BASE_BRANCH" \
  --head "$HEAD_BRANCH" \
  --title "{feature-name}: {one-line summary from plan}" \
  --body "$(cat .pr-body.md)"
```

PR body template:
```markdown
## Summary
{plan summary paragraph}

## Changes
{auto-generated from task list: bullet per task with files affected}

## ADRs
{list of ADRs created during this plan}

## Test Plan
{from plan test plan section}

## Smoke Test Results
{output from integration verification}
```

**Draft PR created. Stop execution here.** Do not mark it ready, approve, merge, enable auto-merge, or push to the base/default branch from this skill, even if asked to continue. The owner must handle those as a separate authorized review-and-merge operation.

Before any later merge, the authorized owner or repository-controlled merge workflow must independently establish:
- Explicit owner authorization for this PR and merge, not only authorization to implement.
- Effective branch protection, required approvals and required CI checks (not merely a successful local run).
- Every required check was performed on the current PR head SHA and applicable merge/merge-queue revision. New commits, base changes or stale/pending checks invalidate earlier acceptance.
- The reviewed requirements and permitted scope are met. An AI reviewer verdict is advisory, not an acceptance receipt.

Leave the draft PR open and return its URL, current PR head SHA, completed local checks, and pending approvals/checks to the owner. The model's statements are not acceptance evidence; repository settings enforce the actual boundary. If checks are missing, failed, stale, or unavailable, report **PENDING / NOT MERGED**. Do not bypass repository policy.

---

## Step 7: Post-merge metrics (separate owner-controlled follow-up)

Do not perform this step as part of `/plan-pipeline:execute`. Only after an authorized owner verifies the actual PR state (for example with `gh pr view <number> --json state,mergedAt,mergeCommit`, checking MERGED plus the expected commit) and gives explicit permission, switch back to develop/main. Archive the plan from `docs/plans/plan-{name}.md` to `docs/plans/completed/plan-{name}.md`, mark it completed, and update `docs/plans/metrics/{name}.json` with execution data:
- Task count and per-layer breakdown
- TDD task count
- Diff stats (files changed, lines added/removed)
- Quality gate results (pass/fail, fix attempts)
- Smoke test results
- Drift score (0-1, how closely implementation matched plan)
- PR data (number, merge commit, timestamp)

Commit metrics update.

---

## Step 8: Worktree cleanup (separate owner-controlled follow-up)

Only after a confirmed merge and explicit owner approval for cleanup may the branch or worktree be removed. Do not delete the branch, and do not remove the worktree, while the PR is open or its merge status is uncertain. Inspect `git status` and confirm there are no uncommitted or unpushed changes before cleanup; otherwise stop and report the remaining state.

```bash
git worktree remove .worktrees/{plan-name}
```

---

## Usage

```
/plan-pipeline:execute
```

Picks up the most recent validated plan. Or specify:

```
/plan-pipeline:execute plan-user-authentication
```

## Output

```
Setting up worktree: .worktrees/user-authentication
Branch: feature/user-authentication

TDD scaffolding: 2 tasks marked TDD
  ✓ Written failing tests for: auth-token-validation
  ✓ Written failing tests for: refresh-token-rotation
  Committed: "test: failing tests for auth pipeline (TDD)"

Executing Layer 1 (3 tasks, parallel)...
  [agent-1] Implementing: JWT token generation service
  [agent-2] Implementing: User session model
  [agent-3] Implementing: Auth middleware
  ✓ Layer 1 complete. 3 commits.

Drift check: Layer 1... ✓ No drift detected.

Executing Layer 2 (2 tasks, parallel)...
  [agent-4] Implementing: Login endpoint
  [agent-5] Implementing: Refresh endpoint
  ✓ Layer 2 complete. 2 commits.

Quality gate...
  ✓ Lint passed
  ✓ Type check passed
  ✓ Tests: 47 passed, 0 failed

Smoke test...
  ✓ GraphQL introspection: OK
  ✓ POST /api/auth/login: 200
  ✓ POST /api/auth/refresh: 200

Pre-PR docs...
  ✓ PRD reconciled (1 minor deviation noted)
  ✓ Plan marked in-review (archival pending accepted merge)

PR created: #142 "user-authentication: JWT auth with refresh token rotation"
Draft PR created. Ready-for-review transition and merge both require separate owner-controlled actions.

Post-merge metrics: PENDING. Worktree retained.
Status: PR awaiting review; release not yet confirmed.
```

## When to use

After `/plan-pipeline:validate` confirms all issues are resolved. Never skip validation: executing an unvalidated plan skips the independent review that catches ~18 issues on average.

## Pipeline position

```
/plan-pipeline:ceo-review    → product direction locked
/plan-pipeline:eng-review    → architecture locked
/plan-pipeline:start         → produce implementation plan
/plan-pipeline:validate      → validate before execution
/plan-pipeline:execute       → execute to draft PR handoff    ← you are here
```
