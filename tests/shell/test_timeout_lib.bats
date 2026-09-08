#!/usr/bin/env bats

setup() {
  export REPO_ROOT
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../.." && pwd)"
  export LIB="${REPO_ROOT}/scripts/lib/timeout.sh"
}

@test "run_with_timeout returns the command status when it finishes in time" {
  run bash -c "source '${LIB}'; run_with_timeout 5 true"
  [ "${status}" -eq 0 ]

  run bash -c "source '${LIB}'; run_with_timeout 5 sh -c 'exit 7'"
  [ "${status}" -eq 7 ]
}

@test "run_with_timeout returns 124 when the command outlives the budget" {
  # 124 is what coreutils returns, so callers branch on the status without
  # knowing which of the three implementations ran.
  run bash -c "source '${LIB}'; run_with_timeout 1 sleep 10"
  [ "${status}" -eq 124 ]
}

@test "timeout_implementation names the path that will actually run" {
  run bash -c "source '${LIB}'; timeout_implementation"
  [ "${status}" -eq 0 ]
  case "${output}" in
    timeout|gtimeout|shell-fallback) ;;
    *) printf 'unexpected implementation: %s\n' "${output}"; return 1 ;;
  esac
}

@test "the shell fallback behaves like coreutils when neither binary exists" {
  # The path that actually runs on a stock Mac. PATH is stripped to a shim
  # directory so `command -v timeout` and `gtimeout` both miss.
  shim="${BATS_TEST_TMPDIR}/shim"
  mkdir -p "${shim}"
  for tool in bash sh sleep date kill true; do
    target="$(command -v "${tool}" 2>/dev/null || true)"
    [ -n "${target}" ] || continue
    ln -sf "${target}" "${shim}/${tool}"
  done

  run env PATH="${shim}" bash -c "source '${LIB}'; timeout_implementation"
  [ "${status}" -eq 0 ]
  [ "${output}" = "shell-fallback" ]

  run env PATH="${shim}" bash -c "source '${LIB}'; run_with_timeout 1 sleep 10"
  [ "${status}" -eq 124 ]

  run env PATH="${shim}" bash -c "source '${LIB}'; run_with_timeout 5 sh -c 'exit 7'"
  [ "${status}" -eq 7 ]
}
