#!/usr/bin/env bats

# The pre-push gate checks the commits being pushed, not whatever the working
# tree holds. Each test drives the gate in a throwaway repository with a make
# stub, so no worktree is ever added to the real one.

ZERO=0000000000000000000000000000000000000000

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../.." && pwd)"
  WORK="$(cd "$BATS_TEST_TMPDIR" && pwd -P)"
  export TEST_BIN="$WORK/bin"
  mkdir -p "$TEST_BIN"
  cat >"$TEST_BIN/make" <<'STUB'
#!/usr/bin/env bash
printf 'make %s in %s at %s\n' "$*" "$(pwd -P)" "$(git rev-parse HEAD)"
exit "${MAKE_STUB_STATUS:-0}"
STUB
  chmod +x "$TEST_BIN/make"

  REPO="$WORK/repo"
  mkdir -p "$REPO/scripts/hooks" "$REPO/scripts/lib"
  cp "$REPO_ROOT/scripts/hooks/lib.sh" "$REPO_ROOT/scripts/hooks/run-local-ci.sh" "$REPO/scripts/hooks/"
  cp "$REPO_ROOT/scripts/lib/shell-cli.sh" "$REPO/scripts/lib/"
  git -C "$REPO" init -q
  git -C "$REPO" -c user.name=t -c user.email=t@example.test -c commit.gpgsign=false commit -q --allow-empty -m one
  FIRST="$(git -C "$REPO" rev-parse HEAD)"
  git -C "$REPO" add scripts
  git -C "$REPO" -c user.name=t -c user.email=t@example.test -c commit.gpgsign=false commit -q -m two
  SECOND="$(git -C "$REPO" rev-parse HEAD)"
}

gate() {
  run env -u APIM_SIMULATOR_LOCAL_CI_IN_PROGRESS -u APIM_SIMULATOR_SKIP_HOOKS \
    -u GIT_DIR -u GIT_INDEX_FILE -u GIT_WORK_TREE \
    PATH="$TEST_BIN:$PATH" APIM_SIMULATOR_PUSHED_REFS="$1" \
    "$REPO/scripts/hooks/run-local-ci.sh" --execute
}

worktree_count() {
  git -C "$REPO" worktree list | wc -l | tr -d ' '
}

@test "a pushed commit other than HEAD is checked in a worktree at that commit" {
  gate "refs/heads/feature $FIRST refs/heads/feature $ZERO"

  [ "$status" -eq 0 ]
  [[ "$output" == *"make local-ci in "*"/checkout at $FIRST"* ]]
  [ "$(worktree_count)" -eq 1 ]
}

@test "pushing the clean checked-out HEAD is checked in place" {
  gate "refs/heads/main $SECOND refs/heads/main $FIRST"

  [ "$status" -eq 0 ]
  [[ "$output" == *"make local-ci in $REPO at $SECOND"* ]]
}

@test "pushing HEAD with uncommitted changes is checked in a worktree" {
  echo dirty >"$REPO/untracked.txt"

  gate "refs/heads/main $SECOND refs/heads/main $FIRST"

  [ "$status" -eq 0 ]
  [[ "$output" == *"/checkout at $SECOND"* ]]
  [[ "$output" != *"in $REPO at"* ]]
}

@test "a push of only branch deletions has nothing to check" {
  gate "(delete) $ZERO refs/heads/old $FIRST"

  [ "$status" -eq 0 ]
  [[ "$output" == *"nothing to check"* ]]
  [[ "$output" != *"make local-ci in"* ]]
}

@test "a failing gate on a pushed commit fails the push and removes its worktree" {
  export MAKE_STUB_STATUS=1

  gate "refs/heads/feature $FIRST refs/heads/feature $ZERO"

  [ "$status" -eq 1 ]
  [[ "$output" == *"pre-push gate failed: make local-ci on $FIRST"* ]]
  [ "$(worktree_count)" -eq 1 ]
}
