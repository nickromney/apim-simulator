#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/hooks/lib.sh
source "${SCRIPT_DIR}/lib.sh"
# shellcheck disable=SC1091
source "${HOOKS_REPO_ROOT}/scripts/lib/shell-cli.sh"

# shellcheck disable=SC2329
usage() {
  cat <<EOF
Usage: ${0##*/} [--dry-run] [--execute] [--] [FILE...]

Runs Ruff checks for staged Python files.

$(shell_cli_standard_options)
EOF
}

shell_cli_parse_standard_only usage "$@" || exit 1
shell_cli_maybe_execute_or_preview_summary usage \
  "would run ruff format --check and ruff check for staged Python files"
set -- "${SHELL_CLI_ARGS[@]}"

if hook_skip_requested; then
  hook_print_skip_and_exit
fi

python_files=()
for file in "$@"; do
  case "${file}" in
    *.py)
      python_files+=("${file}")
      ;;
  esac
done

if [[ "${#python_files[@]}" -eq 0 ]]; then
  hook_ok "ruff: no staged Python files"
  exit 0
fi

cd "${HOOKS_REPO_ROOT}"

if [[ -x "${HOOKS_REPO_ROOT}/.venv/bin/ruff" ]]; then
  "${HOOKS_REPO_ROOT}/.venv/bin/ruff" format --check "${python_files[@]}"
  "${HOOKS_REPO_ROOT}/.venv/bin/ruff" check "${python_files[@]}"
elif command -v ruff >/dev/null 2>&1; then
  ruff format --check "${python_files[@]}"
  ruff check "${python_files[@]}"
else
  hook_warn "ruff not found in PATH or .venv; skipping staged Python lint until dependencies are installed"
  exit 0
fi

hook_ok "ruff: ${#python_files[@]} staged Python file(s)"
