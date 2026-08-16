#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# shellcheck source=/dev/null
source "${SCRIPT_DIR}/lib/shell-cli.sh"

SHELLCHECK_BIN="${SHELLCHECK_BIN:-shellcheck}"

# shellcheck disable=SC2329 # invoked by name through the shell_cli_* helpers
usage() {
  cat <<EOF
Usage: ${0##*/} [--dry-run] [--execute]

Run shellcheck over every tracked shell script.

\`lint-shell\` runs audit-shell-scripts.sh, which checks *conventions* and never
invokes shellcheck. shellcheck ran only in the lefthook pre-commit hook, over
*staged* files, so a script was checked when first committed and never again --
9 of 35 had drifted by the time this was added, and one of the findings was a
prose backtick inside an unquoted heredoc that made \`--help\` execute commands.

\`-x\` follows sourced files, matching the pre-commit hook, so the two surfaces
cannot disagree about whether the tree is clean.

$(shell_cli_standard_options)
EOF
}

shell_cli_handle_standard_no_args usage "would run shellcheck over tracked shell scripts under ${ROOT_DIR}" "$@"

if ! command -v "${SHELLCHECK_BIN}" >/dev/null 2>&1; then
  echo "FAIL shellcheck not found in PATH" >&2
  exit 1
fi

shell_files=()
while IFS= read -r -d '' file; do
  [[ -f "${ROOT_DIR}/${file}" ]] || continue
  shell_files+=("${file}")
done < <(git -C "${ROOT_DIR}" ls-files -z -- '*.sh')

if [[ "${#shell_files[@]}" -eq 0 ]]; then
  echo "WARN no shell scripts found under ${ROOT_DIR}"
  exit 0
fi

echo "INFO checking ${#shell_files[@]} tracked shell script(s)"

failed=0
cd "${ROOT_DIR}"
for file in "${shell_files[@]}"; do
  "${SHELLCHECK_BIN}" -x "${file}" || failed=1
done

if [[ "${failed}" -ne 0 ]]; then
  echo "FAIL shellcheck reported findings; fix them or add a scoped disable with a reason" >&2
  exit 1
fi

echo "OK   shellcheck"
