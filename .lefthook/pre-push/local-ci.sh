#!/usr/bin/env bash
# Pre-push entry point. Lefthook skips pre-push `commands` and `jobs` whenever
# its own diff of the checked-out branch against @{push} is empty, which is
# every push of a branch other than the one checked out. Scripts are not
# skipped that way, so the gate lives here and checks the refs git is pushing.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/lib/shell-cli.sh"

run_gate() {
  APIM_SIMULATOR_PUSHED_REFS="$(cat)"
  export APIM_SIMULATOR_PUSHED_REFS
  exec "${REPO_ROOT}/scripts/hooks/run-local-ci.sh" --execute
}

# lefthook.yml sets this for the hook, which passes git's remote name and URL
# rather than this script's options.
if [[ "${APIM_SIMULATOR_PRE_PUSH_HOOK:-}" == "1" ]]; then
  run_gate
fi

# shellcheck disable=SC2329
usage() {
  cat <<EOF
Usage: ${0##*/} [--dry-run] [--execute]

Pre-push hook entry point: reads git's pushed refs on stdin and runs
scripts/hooks/run-local-ci.sh on each pushed commit.

$(shell_cli_standard_options)
EOF
}

shell_cli_handle_standard_no_args usage \
  "would read pushed refs from stdin and run scripts/hooks/run-local-ci.sh --execute" \
  "$@"

run_gate
