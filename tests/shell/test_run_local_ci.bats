#!/usr/bin/env bats

setup() {
  export REPO_ROOT
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../.." && pwd)"
  export TEST_BIN="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$TEST_BIN"
  # A make stub that reports what the gate's environment would hand to the tests.
  cat >"$TEST_BIN/make" <<'STUB'
#!/usr/bin/env bash
printf 'make %s GIT_DIR=%s GIT_INDEX_FILE=%s\n' "$*" "${GIT_DIR:-unset}" "${GIT_INDEX_FILE:-unset}"
STUB
  chmod +x "$TEST_BIN/make"
}

@test "the pre-push gate hides git's hook variables from the tests it runs" {
  # Unset the skip switches: this test may itself be running inside the gate.
  run env -u APIM_SIMULATOR_LOCAL_CI_IN_PROGRESS -u APIM_SIMULATOR_SKIP_HOOKS \
    PATH="$TEST_BIN:$PATH" GIT_DIR="$REPO_ROOT/.git" GIT_INDEX_FILE="$REPO_ROOT/.git/index" \
    "$REPO_ROOT/scripts/hooks/run-local-ci.sh" --execute

  [ "$status" -eq 0 ]
  [[ "$output" == *"make local-ci GIT_DIR=unset GIT_INDEX_FILE=unset"* ]]
}
