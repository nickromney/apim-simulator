#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# shellcheck source=/dev/null
source "${SCRIPT_DIR}/lib/shell-cli.sh"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/lib/timeout.sh"

# Modules small and self-contained enough for a run to finish and a survivor to
# be actionable. app/policy.py is deliberately absent: at 3,200 lines a run is
# long enough that nobody does it twice.
DEFAULT_MODULES=(
  app.urls
  app.effective_policy
  app.named_values
  app.backend_pool
  app.apim_expr
)

MODULES=()
BUDGET_SECONDS="${MUTATION_BUDGET_SECONDS:-1800}"
# mutmut forks a pytest per mutant and defaults to every core. Unbounded, this
# crashed four Python processes on a development Mac -- and a killed child is
# scored as nothing rather than as a failure, so the cap protects the score as
# well as the host. Two by default; raise it deliberately on a machine with
# headroom, or when nothing else is running.
MAX_CHILDREN="${MUTATION_MAX_CHILDREN:-}"
FAIL_ON_SURVIVORS=1
# mutmut runs each mutant by forking without an exec, and on this macOS host a
# reproducible share of children die on SIGSEGV before they report anything. A
# reconcile pass re-runs exactly those mutants one at a time in a fresh process,
# where they do not crash, and folds the verdicts back into the score.
RECONCILE="${MUTATION_RECONCILE:-0}"
REPORT_DIR="${MUTATION_REPORT_DIR:-.run/mutation}"

# shellcheck disable=SC2329 # invoked by name through the shell_cli_* helpers
usage() {
  cat <<EOF
Usage: ${0##*/} [--module NAME]... [--budget SECONDS] [--max-children N] [--no-fail] [--reconcile] [--dry-run] [--execute]

Run mutation testing over one module at a time and report a score for each.

Coverage says a line ran. Mutation testing says whether the suite would notice
if that line changed. A mutant that makes the suite fail is killed; one that
survives is an assertion gap on that exact line.

Name the module. A run that drags in suites which cannot kill a single one of
its mutants costs minutes and buys nothing. With no --module, the curated list
is: ${DEFAULT_MODULES[*]}

Each run is bounded through run_with_timeout, which works without GNU
coreutils. A bare 'timeout' is not on stock macOS: it reports "command not
found" per module and still leaves the loop exiting 0, so a batch that scored
nothing looks like a batch that passed.

Runs are capped at 2 concurrent children by default. mutmut forks a pytest per
mutant and otherwise takes every core, which crashed four Python processes on a
development Mac; a killed child scores as nothing rather than as a failure.
Raise --max-children deliberately, on a machine with headroom.

Logs for every run are kept under ${REPORT_DIR}, which is git-ignored. A module
that scores nothing is a runner problem, not a perfect suite, so its log is
named in the output rather than discarded.

--reconcile re-runs the mutants mutmut could not report on, one at a time in a
fresh process. mutmut forks without an exec, and on this macOS host a
reproducible share of those children die on SIGSEGV; the same mutant run in its
own process reports a verdict. The pass costs one full suite run per mutant, so
it is opt-in.

Exits non-zero when mutants survive. --no-fail reports the score and exits 0,
which is how a baseline is measured before the gaps are closed.

$(shell_cli_standard_options)
EOF
}

shell_cli_init_standard_flags
while [[ $# -gt 0 ]]; do
  if shell_cli_handle_standard_flag usage "$1"; then
    shift
    continue
  fi
  case "$1" in
    --module)
      [[ $# -ge 2 ]] || { shell_cli_missing_value "$(shell_cli_script_name)" "--module"; exit 1; }
      MODULES+=("$2")
      shift 2
      ;;
    --budget)
      [[ $# -ge 2 ]] || { shell_cli_missing_value "$(shell_cli_script_name)" "--budget"; exit 1; }
      BUDGET_SECONDS="$2"
      shift 2
      ;;
    --max-children)
      [[ $# -ge 2 ]] || { shell_cli_missing_value "$(shell_cli_script_name)" "--max-children"; exit 1; }
      MAX_CHILDREN="$2"
      shift 2
      ;;
    --no-fail)
      FAIL_ON_SURVIVORS=0
      shift
      ;;
    --reconcile)
      RECONCILE=1
      shift
      ;;
    *)
      shell_cli_unknown_flag "$(shell_cli_script_name)" "$1"
      exit 1
      ;;
  esac
done

if [[ "${#MODULES[@]}" -eq 0 ]]; then
  MODULES=("${DEFAULT_MODULES[@]}")
fi

# shellcheck disable=SC2329 # invoked by name through the shell_cli_* helpers
preview() {
  shell_cli_print_dry_run_summary \
    "would mutate ${#MODULES[@]} module(s) with a ${BUDGET_SECONDS}s budget each, logging to ${REPORT_DIR}: ${MODULES[*]}"
}

# First field of the `uniq -c` row whose status begins with this word, or 0.
# mutmut spells two statuses as two words ("no tests", "not checked"), so the
# match is on the first word only.
_count() {
  printf '%s\n' "$1" | awk -v want="$2" '$2 == want { print $1; found = 1 } END { if (!found) print 0 }'
}

# Mutants mutmut recorded with an exit code it cannot turn into a verdict.
# The meta file it writes per source file holds the raw code for every mutant.
_unreconciled_keys() {
  local meta="mutants/${1//.//}.py.meta"
  [[ -f "${meta}" ]] || return 0
  jq -r '.exit_code_by_key | to_entries[]
         | select(.value != null and (.value | IN(0, 1, 2, 33, 34, 35, 36, 37, -24, 24, 152, 255)) | not)
         | .key' "${meta}"
}

# Re-run one mutant in its own process. The mutated tree is only on the path
# through PYTHONPATH: without it pytest imports the installed package and the
# trampoline never sees its own module, so every mutant looks like a survivor.
# Returns 0 when the suite noticed the mutant, 1 when it did not.
_reconcile_one() {
  local key="$1" status=0
  MUTANT_UNDER_TEST="${key}" PYTHONPATH="${ROOT_DIR}/mutants" \
    uv run --project "${ROOT_DIR}" --extra dev pytest \
    -p no:cacheprovider -m "not integration and not repo" -q -x >/dev/null 2>&1 || status=$?
  [[ "${status}" -ne 0 ]]
}

main() {
  local module="" report="" scores="" status=0
  local killed=0 survived=0 no_tests=0 timeout_n=0 segfault=0 suspicious=0 not_checked=0
  local total=0 accounted=0 unreconciled=0
  local reconciled_killed=0 reconciled_survived=0
  local total_survived=0
  local -a rows=()

  cd "${ROOT_DIR}"
  mkdir -p "${REPORT_DIR}"
  if [[ -z "${MAX_CHILDREN}" ]]; then
    MAX_CHILDREN=2
  fi
  printf 'timeout implementation: %s | max children: %s\n\n' \
    "$(timeout_implementation)" "${MAX_CHILDREN}"

  for module in "${MODULES[@]}"; do
    printf 'mutating %s ...\n' "${module}"
    report="${REPORT_DIR}/${module}.log"
    status=0
    # mutmut matches with fnmatch, and a mutant is <module>.x_<fn>__mutmut_<n>.
    # A bare module name matches nothing at all.
    run_with_timeout "${BUDGET_SECONDS}" \
      uv run --project "${ROOT_DIR}" --extra dev mutmut run --max-children "${MAX_CHILDREN}" \
      "${module}.*" >"${report}" 2>&1 || status=$?

    if [[ "${status}" -eq 124 ]]; then
      rows+=("${module}	TIMEOUT after ${BUDGET_SECONDS}s	log: ${report}")
      continue
    fi

    # Score from `results --all`, not from the run's own output. A cached mutant
    # is not re-printed by the run, so parsing the run makes a second invocation
    # report "no mutants scored" for a module that is fully measured.
    scores="$(uv run --project "${ROOT_DIR}" --extra dev mutmut results --all true 2>/dev/null \
      | grep -F "${module}." | sed -E 's/.*: //' | sort | uniq -c)"
    killed="$(_count "${scores}" killed)"
    survived="$(_count "${scores}" survived)"
    no_tests="$(_count "${scores}" no)"          # "no tests"
    timeout_n="$(_count "${scores}" timeout)"
    segfault="$(_count "${scores}" segfault)"
    suspicious="$(_count "${scores}" suspicious)"
    not_checked="$(_count "${scores}" not)"      # "not checked"
    total="$(printf '%s\n' "${scores}" | awk '{s += $1} END {print s + 0}')"

    # Reconcile before reporting. A percentage over killed+survived alone is a
    # number derived from partial bookkeeping: on macOS a large share of mutants
    # can come back "segfault" from the fork-without-exec worker, and silently
    # dropping them turns a half-measured module into a flattering score.
    # A mutant that makes the suite hang is a mutant the suite noticed, so a
    # timeout counts as killed. segfault, suspicious and unchecked do not: the
    # runner has no evidence either way about those.
    killed=$((killed + timeout_n))
    accounted=$((killed + survived + no_tests))
    unreconciled=$((total - accounted))
    if [[ "${unreconciled}" -ne 0 && "${RECONCILE}" -eq 1 ]]; then
      printf '  reconciling %d mutant(s) out of process ...\n' "${unreconciled}"
      reconciled_killed=0
      reconciled_survived=0
      while IFS= read -r key; do
        [[ -n "${key}" ]] || continue
        if _reconcile_one "${key}"; then
          reconciled_killed=$((reconciled_killed + 1))
        else
          reconciled_survived=$((reconciled_survived + 1))
          printf '    survived: %s\n' "${key}"
        fi
      done < <(_unreconciled_keys "${module}")
      killed=$((killed + reconciled_killed))
      survived=$((survived + reconciled_survived))
      accounted=$((killed + survived + no_tests))
      unreconciled=$((total - accounted))
    fi

    if [[ "${unreconciled}" -ne 0 ]]; then
      rows+=("$(printf '%s\tNOT SCORED\t%d of %d unreconciled (segfault %d, suspicious %d, unchecked %d)\tlog: %s' \
        "${module}" "${unreconciled}" "${total}" "${segfault}" "${suspicious}" "${not_checked}" "${report}")")
      continue
    fi

    if [[ $((killed + survived)) -eq 0 ]]; then
      # Do not report this as a pass. Nothing was measured.
      rows+=("${module}	NOT SCORED	${no_tests} unreached	log: ${report}")
      continue
    fi

    rows+=("$(printf '%s\t%d killed\t%d survived\t%d unreached\t%d%%' \
      "${module}" "${killed}" "${survived}" "${no_tests}" \
      $((killed * 100 / (killed + survived))))")
    total_survived=$((total_survived + survived))
  done

  printf '\n%s\n' "--- mutation score ---"
  printf '%s\n' "${rows[@]}"

  if printf '%s\n' "${rows[@]}" | grep -q 'NOT SCORED\|TIMEOUT'; then
    printf '\nA module produced no score. Read its log before trusting this run.\n' >&2
    return 1
  fi
  if [[ "${FAIL_ON_SURVIVORS}" -eq 1 && "${total_survived}" -gt 0 ]]; then
    printf '\n%d mutant(s) survived.\n' "${total_survived}" >&2
    return 1
  fi
  return 0
}

# The helper only guards: it prints usage or the preview and exits when this is
# not an --execute run, and returns otherwise. main is called here.
shell_cli_maybe_execute_or_preview usage preview
main
