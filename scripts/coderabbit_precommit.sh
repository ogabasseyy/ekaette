#!/usr/bin/env bash
set -euo pipefail

log() {
  printf '[coderabbit-precommit] %s\n' "$*"
}

fail() {
  log "$*"
  exit 1
}

require_command() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1 || fail "Missing required command: $cmd"
}

require_command git
require_command coderabbit
require_command codex
require_command jq
require_command mktemp

parse_result_state() {
  local file_path="$1"
  local state

  state="$(jq -r '.result // empty' "$file_path")"
  if [[ -z "$state" || "$state" == "null" ]]; then
    fail "Missing result in schema output: $file_path"
  fi
  printf '%s\n' "$state"
}

has_unstaged_changes() {
  if ! git diff --quiet; then
    return 0
  fi

  if [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
    return 0
  fi

  return 1
}

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

if [[ "${SKIP_CODERABBIT_PRECOMMIT:-0}" == "1" ]]; then
  log "Skipping because SKIP_CODERABBIT_PRECOMMIT=1."
  exit 0
fi

if git diff --cached --quiet; then
  exit 0
fi

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/coderabbit-precommit.XXXXXX")"
stash_label="coderabbit-precommit-$(date +%s)-$$"
stash_ref=""
stashed_unstaged=0
review_tree="$tmp_dir/review-worktree"
review_patch="$tmp_dir/review.patch"

prepare_review_tree() {
  if [[ -d "$review_tree" ]]; then
    git worktree remove --force "$review_tree" >/dev/null 2>&1 || true
  fi
  git diff --cached >"$review_patch"
  git worktree add --detach "$review_tree" HEAD >/dev/null
  if [[ -s "$review_patch" ]]; then
    git -C "$review_tree" apply --whitespace=nowarn "$review_patch"
  fi
}

cleanup() {
  local status="$?"
  local restore_failed=0

  if [[ -d "$review_tree" ]]; then
    git worktree remove --force "$review_tree" >/dev/null 2>&1 || true
  fi

  if [[ "$stashed_unstaged" -eq 1 ]]; then
    if [[ -z "$stash_ref" ]]; then
      log "Could not find the saved unstaged-change stash."
      restore_failed=1
    elif ! git stash pop -q "$stash_ref" >/dev/null 2>&1; then
      log "Failed to restore unstaged changes cleanly."
      log "Resolve the stash manually from: $stash_label"
      restore_failed=1
    fi
  fi

  rm -rf "$tmp_dir"
  trap - EXIT

  if [[ "$restore_failed" -eq 1 ]]; then
    exit 1
  fi

  exit "$status"
}

trap cleanup EXIT

if has_unstaged_changes; then
  log "Stashing unstaged changes to review only the staged snapshot."
  git stash push -q --keep-index --include-untracked -m "$stash_label"
  stash_ref="$(git stash list --grep="$stash_label" --format="%gd" | head -n1)"
  if [[ -z "$stash_ref" ]]; then
    fail "Could not locate the stash created for unstaged changes."
  fi
  stashed_unstaged=1
fi

prepare_review_tree

fix_schema="$tmp_dir/fix-schema.json"
verify_schema="$tmp_dir/verify-schema.json"
first_report="$tmp_dir/coderabbit-initial.txt"
first_stderr="$tmp_dir/coderabbit-initial.stderr"
fix_result="$tmp_dir/fix-result.json"
verify_report="$tmp_dir/coderabbit-verify.txt"
verify_stderr="$tmp_dir/coderabbit-verify.stderr"
verify_result="$tmp_dir/verify-result.json"

cat >"$fix_schema" <<'JSON'
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "result": {
      "type": "string",
      "enum": ["clean", "fixed", "blocked"]
    },
    "summary": {
      "type": "string"
    }
  },
  "required": ["result", "summary"]
}
JSON

cat >"$verify_schema" <<'JSON'
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "result": {
      "type": "string",
      "enum": ["clean", "blocked"]
    },
    "summary": {
      "type": "string"
    }
  },
  "required": ["result", "summary"]
}
JSON

log "Running CodeRabbit on staged changes."
if ! (
  cd "$review_tree"
  coderabbit review --prompt-only -t uncommitted
) >"$first_report" 2>"$first_stderr"; then
  cat "$first_stderr" >&2
  fail "CodeRabbit review failed."
fi

log "Running Codex autofix pass for blocking findings."
if ! codex -a never -s workspace-write exec --output-schema "$fix_schema" -o "$fix_result" - <<EOF; then
You are running inside a git pre-commit hook for the repository at:
$repo_root

The staged snapshot is the only code currently present in the working tree. Any previously unstaged
changes have been stashed away.

Your task is to read the CodeRabbit prompt-only report at:
$first_report

Treat only the following as blocking for commit:
- correctness bugs
- crash or exception risks
- data loss or corruption
- security issues
- test failures
- API or schema contract mismatches
- concurrency issues
- clearly severe performance regressions

Ignore non-blocking items such as style, naming, formatting, comments, and optional refactors.

Workflow:
1. Read the CodeRabbit report.
2. If there are no blocking issues, make no code changes.
3. If there are blocking issues, implement the smallest safe fix in this repository.
4. Run only fast, targeted validation if needed.
5. Stage any modified files with git add.
6. Do not create a commit, amend a commit, push, or open interactive tools.

Return schema output only.
Set result to "clean" if there were no blocking issues, "fixed" if you changed files and believe
the blocking issues are addressed, or "blocked" if you cannot safely resolve them in this hook.
EOF
  fail "Codex autofix pass failed."
fi

fix_state="$(parse_result_state "$fix_result")"

case "$fix_state" in
  clean|fixed|blocked) ;;
  *)
    fail "Unexpected autofix result state: $fix_state"
    ;;
esac

if [[ "$fix_state" == "blocked" ]]; then
  log "Codex could not safely resolve the blocking findings."
  cat "$fix_result"
  exit 1
fi

if [[ "$fix_state" == "fixed" ]]; then
  git add -u
  if [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
    log "Autofix created untracked files. Review and stage them manually."
    git status --short --untracked-files=all >&2
    exit 1
  fi
fi

if [[ "$fix_state" == "clean" ]]; then
  log "No blocking CodeRabbit findings for the staged snapshot."
  exit 0
fi

log "Re-running CodeRabbit after Codex fixes."
prepare_review_tree
if ! (
  cd "$review_tree"
  coderabbit review --prompt-only -t uncommitted
) >"$verify_report" 2>"$verify_stderr"; then
  cat "$verify_stderr" >&2
  fail "CodeRabbit verification pass failed."
fi

log "Verifying whether any blocking findings remain."
if ! codex -a never -s read-only exec --output-schema "$verify_schema" -o "$verify_result" - <<EOF; then
Read the CodeRabbit prompt-only report at:
$verify_report

Do not modify any files.

Treat only the following as blocking for commit:
- correctness bugs
- crash or exception risks
- data loss or corruption
- security issues
- test failures
- API or schema contract mismatches
- concurrency issues
- clearly severe performance regressions

Ignore non-blocking items such as style, naming, formatting, comments, and optional refactors.

Return schema output only.
Set result to "clean" if no blocking findings remain, or "blocked" if blocking findings still
remain.
EOF
  fail "Codex verification pass failed."
fi

verify_state="$(parse_result_state "$verify_result")"

case "$verify_state" in
  clean|blocked) ;;
  *)
    fail "Unexpected verification result state: $verify_state"
    ;;
esac

if [[ "$verify_state" == "clean" ]]; then
  log "CodeRabbit verification passed."
  exit 0
fi

log "Blocking CodeRabbit findings remain after the autofix pass."
cat "$verify_result"
cat "$verify_report"
exit 1
