#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/hooks/lib.sh
source "${SCRIPT_DIR}/lib.sh"
# shellcheck disable=SC1091
source "${HOOKS_REPO_ROOT}/scripts/lib/shell-cli.sh"

ZERO_SHA="0000000000000000000000000000000000000000"

# shellcheck disable=SC2329
usage() {
  cat <<EOF
Usage: ${0##*/} [--dry-run] [--execute]

Runs the repo local CI gate (make local-ci) used by the pre-push hook.

APIM_SIMULATOR_PUSHED_REFS holds the lines git gives a pre-push hook
("<local ref> <local sha> <remote ref> <remote sha>"). Each pushed commit is
checked: in place when it is the checked-out HEAD and the working tree is
clean, otherwise in a temporary worktree at that commit. Without pushed refs
the gate checks the working tree as it is.

$(shell_cli_standard_options)
EOF
}

shell_cli_handle_standard_no_args usage \
  "would run pre-push local CI gate: make local-ci on each pushed commit" \
  "$@"

if hook_skip_requested; then
  hook_print_skip_and_exit
fi

if [[ "${APIM_SIMULATOR_LOCAL_CI_IN_PROGRESS:-}" == "1" ]]; then
  hook_warn "APIM_SIMULATOR_LOCAL_CI_IN_PROGRESS=1; skipping run-local-ci.sh to avoid recursive local CI"
  exit 0
fi

cd "${HOOKS_REPO_ROOT}"

cat <<'EOF'
APIM Simulator pre-push local CI gate

Running:
  make local-ci

Skip only when you have a reason:
  LEFTHOOK=0 git push
  APIM_SIMULATOR_SKIP_HOOKS=1 git push
  git push --no-verify
EOF

# Git exports GIT_DIR and friends to hooks, and from a linked worktree GIT_DIR
# points at the shared .git. Tests that build throwaway repositories would act
# on this one instead, so clear them; git rediscovers the checkout from the cwd.
# shellcheck disable=SC2046 # one variable name per word
unset $(git rev-parse --local-env-vars)

export APIM_SIMULATOR_LOCAL_CI_IN_PROGRESS=1
export UV_CACHE_DIR="${UV_CACHE_DIR:-${TMPDIR:-/tmp}/apim-simulator-uv-cache}"

# The commits being pushed, once each; branch deletions push nothing to check.
pushed_commits() {
  local local_sha
  while read -r _ local_sha _ _; do
    if [[ -n "${local_sha:-}" && "${local_sha}" != "${ZERO_SHA}" ]]; then
      printf '%s\n' "${local_sha}"
    fi
  done <<<"${APIM_SIMULATOR_PUSHED_REFS:-}" | sort -u
}

working_tree_is_head() {
  local sha="$1"
  [[ "$(git rev-parse HEAD)" == "${sha}" && -z "$(git status --porcelain)" ]]
}

run_gate_here() {
  if ! make local-ci; then
    hook_fail "pre-push gate failed: make local-ci"
    exit 1
  fi
}

GATE_WORKTREE=""

remove_gate_worktree() {
  if [[ -n "${GATE_WORKTREE}" ]]; then
    git -C "${HOOKS_REPO_ROOT}" worktree remove --force "${GATE_WORKTREE}/checkout" >/dev/null 2>&1 || true
    rm -rf "${GATE_WORKTREE}"
    GATE_WORKTREE=""
  fi
}
trap remove_gate_worktree EXIT

run_gate_in_worktree() {
  local sha="$1"
  GATE_WORKTREE="$(mktemp -d "${TMPDIR:-/tmp}/apim-simulator-pre-push.XXXXXX")"
  git worktree add --detach --quiet "${GATE_WORKTREE}/checkout" "${sha}"
  echo "Checking pushed commit ${sha} in ${GATE_WORKTREE}/checkout"
  if ! (cd "${GATE_WORKTREE}/checkout" && make local-ci); then
    hook_fail "pre-push gate failed: make local-ci on ${sha}"
    exit 1
  fi
  remove_gate_worktree
}

if [[ -z "${APIM_SIMULATOR_PUSHED_REFS:-}" ]]; then
  run_gate_here
  hook_ok "pre-push gate passed: make local-ci"
  exit 0
fi

commits="$(pushed_commits)"
if [[ -z "${commits}" ]]; then
  hook_ok "pre-push gate: only branch deletions pushed; nothing to check"
  exit 0
fi

for sha in ${commits}; do
  if working_tree_is_head "${sha}"; then
    echo "Checking pushed commit ${sha} in the working tree"
    run_gate_here
  else
    run_gate_in_worktree "${sha}"
  fi
  hook_ok "pre-push gate passed: make local-ci on ${sha}"
done
