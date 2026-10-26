---
name: code-hygiene
description: Use when wrapping up a work session (end of day) and you want the day's changes checked for regressions, best-practice violations, and dead code before they accumulate. Triggers on "/code-hygiene", "clean up today's work", "daily review", "did I introduce any regressions".
---

# Code Hygiene

## Overview

An end-of-day pass over **the day's changes only** that catches three classes of decay before they pile up: **regressions** (does it still build and pass tests?), **best-practice violations** (does it follow this project's standards?), and **dead code** (did today's work orphan anything?).

**Core principle: report first, edit only after approval.** Dead-code deletion and behavioral fixes are easy to get wrong from a diff. This skill produces a findings report and proposes fixes; it does not touch the tree until you say so.

This skill reviews **today's commits + the working tree** — not the whole repo. It is a daily janitor, not a full audit.

## When to Use

- End of a work session, before you stop for the day
- You committed several times today and want a safety net
- "Did I leave anything broken / unused / sloppy?"

**When NOT to use:**
- Mid-task, with a half-finished feature in the tree (regression checks will fail on incomplete work — that's noise, not signal)
- For a full-repo audit → that's a different, much larger job
- For deep correctness bug-hunting on a specific change → use `/code-review` directly

## The Workflow

Run these steps in order. Create a TodoWrite item per step so nothing is skipped.

### 1. Establish the review set

```bash
git log --oneline --since="00:00" --until="now"     # today's commits
git diff HEAD --stat                                 # uncommitted (staged + unstaged)
```

The review set = files touched by today's commits **plus** the current working tree. Build the diff:

```bash
# Oldest commit dated today (fall back to HEAD if none):
BASE=$(git log --since="00:00" --format=%H | tail -1)
git diff "${BASE}^" -- <changed files>   # full day's diff incl. working tree
```

If there are **no** changes today, say so and stop. Don't invent work.

### 2. Load this project's standards

Read `CLAUDE.md` / `AGENTS.md` for the project's stated rules (e.g. *files under 500 lines, no hardcoded secrets, input validation at network boundaries, typed interfaces*). The best-practices pass checks against **these**, not generic opinions. If the repo has a linter config (`.swiftlint.yml`, `.editorconfig`, ESLint, etc.), respect it.

### 3. Regression check (build + tests)

Detect the toolchain and run the **narrowest** build/test that covers the changed area. Do not run the entire test matrix unless the change is broad.

| Project type | Build | Test |
|---|---|---|
| Xcode (this repo) | `xcodebuild -project PatternSpace.xcodeproj -scheme PatternSpace-macOS -configuration Debug build` | `xcodebuild test -project … -scheme … -destination 'platform=macOS,arch=arm64'` (prefer `-only-testing:` for the touched target) |
| Gradle/Android | `./gradlew assembleDebug` | `./gradlew test` (or the touched module) |
| Node | `npm run build` | `npm test` |

Report build/test results **verbatim** — exit code and the failing output. A regression is the highest-severity finding. Never claim "tests pass" without showing the command output (see [[feedback_ci_debugging]] for the `-only-testing:` `()` suffix gotcha and Metal-on-CI caveats).

### 4. Best-practices review

For correctness/quality on the diff, **invoke `/code-review`** rather than re-deriving it here — it's the project's tuned reviewer. Then layer the project-specific checks on top:

- Files that crossed 500 lines today
- New hardcoded secrets/credentials/URLs/tokens
- Network/parsing boundaries added without input validation
- Untyped/`Any` interfaces where a typed contract is expected
- New outbound network behavior (per project security rules — flag, don't assume it's fine)
- Copy-paste duplication introduced today

### 5. Dead-code detection (scoped to today)

Today's work is the most likely source of *new* dead code. Look for:

- **Orphaned by today's edits:** functions/types/files that today's diff stopped referencing. For each removed call site, grep the repo for remaining references:
  ```bash
  git grep -n "symbolName"   # zero hits (outside its own definition) ⇒ candidate dead code
  ```
- **Added but never wired up:** new symbols introduced today with no caller.
- **Unused imports / locals** in changed files.
- **Commented-out blocks** left behind.

If a dedicated tool is available (`periphery` for Swift, `ts-prune`, `deadcode`, etc.), run it and intersect its results with today's changed files. None is installed here by default, so rely on `git grep`.

**Beware false positives:** reflection, `@objc`/selectors, dynamic dispatch, test-only helpers, public API consumed elsewhere, SourceKit phantom "unused" reports. When unsure, mark **"likely dead — confirm"** rather than asserting. The user has been explicit: real dead code should be deleted, not left commented out (see [[feedback_design_and_brainstorming]]) — but only *after* confirmation here.

### 6. Report, then ask

Emit a single report, ordered by severity:

```
## Code Hygiene — <date>  (N files, M commits reviewed)

### 🔴 Regressions
- <build/test failure with verbatim output>   | <file:line>

### 🟠 Best-practice
- <violation> → <stated rule it breaks>        | <file:line>

### 🟡 Dead code (confirm before deleting)
- <symbol> — no remaining references            | <file:line>

### ✅ Clean
- <areas checked that were fine>

### Proposed fixes
1. <fix> — <why> — <safe to auto-apply? y/n>
```

Then **stop and ask** which fixes to apply. Apply only what's approved. Re-run the relevant build/test after editing to prove the fix didn't regress anything (evidence before "done", per [[feedback_ci_debugging]]).

## Quick Reference

| Step | Command / action |
|---|---|
| Scope | `git log --since=00:00` + `git diff HEAD` |
| Standards | read `CLAUDE.md` / `AGENTS.md` + linter config |
| Regression | narrowest build + `-only-testing:` for touched target |
| Best practice | `/code-review` + project rules |
| Dead code | `git grep` each orphaned/new symbol |
| Output | severity-ordered report, then **ask** before editing |

## Common Mistakes

| Mistake | Fix |
|---|---|
| Reviewing the whole repo | Scope to today's diff only |
| "Tests pass" with no output | Paste the verbatim command result |
| Deleting "dead" code from the diff alone | `git grep` first; reflection/selectors/tests hide callers; confirm before deleting |
| Running the full test matrix | Use `-only-testing:` / the touched module |
| Auto-fixing without asking | Report first; edit only on approval |
| Flagging incomplete WIP as a regression | If the tree is mid-task, don't run this skill |
