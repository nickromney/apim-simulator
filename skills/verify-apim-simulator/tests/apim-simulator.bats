#!/usr/bin/env bats
# App-specific tests for the APIM simulator verification skill (the shared contract is in contract.bats).
# docker, make and uv are stubbed on PATH and record any call, so no case builds, starts containers or
# touches real state.

setup() {
  SCRIPT="$BATS_TEST_DIRNAME/../scripts/control.sh"
  BASH32="${VERIFY_TEST_BASH:-/bin/bash}"
  export VERIFY_BASE_DIR="$BATS_TEST_TMPDIR/base"
  export VERIFY_EVIDENCE_ROOT="$BATS_TEST_TMPDIR/evidence"
  export VERIFY_RUN_ID="app-run"
  unset VERIFY_SLOT
  STUB_BIN="$BATS_TEST_TMPDIR/stubs"
  mkdir -p "$STUB_BIN"
  local tool
  for tool in docker make uv; do
    printf '#!/bin/sh\ntouch "%s/touched-%s"\nexit 99\n' "$BATS_TEST_TMPDIR" "$tool" > "$STUB_BIN/$tool"
    chmod +x "$STUB_BIN/$tool"
  done
  export PATH="$STUB_BIN:$PATH"
}

assert_no_writes() {
  [ ! -e "$VERIFY_BASE_DIR" ] || { echo "wrote $VERIFY_BASE_DIR"; return 1; }
  [ ! -e "$VERIFY_EVIDENCE_ROOT" ] || { echo "wrote $VERIFY_EVIDENCE_ROOT"; return 1; }
}

assert_untouched() {
  local tool
  for tool in docker make uv; do
    [ ! -e "$BATS_TEST_TMPDIR/touched-$tool" ] || { echo "$tool was called"; return 1; }
  done
}

# write_state <stack> [slot]: a launched run's state files, without launching anything.
write_state() {
  mkdir -p "$VERIFY_BASE_DIR/app-run/state"
  printf '%s\n' "$1" > "$VERIFY_BASE_DIR/app-run/state/stack"
  printf '%s\n' "${2:-7}" > "$VERIFY_BASE_DIR/app-run/state/slot"
}

@test "launch refuses slot 0 before touching Docker, make or the run dirs" {
  run env VERIFY_SLOT=0 "$BASH32" "$SCRIPT" --execute launch
  [ "$status" -eq 1 ]
  [[ "$output" == *"slot 0 is the user's default stack"* ]]
  assert_no_writes
  assert_untouched
}

@test "verify refuses slot 0 without a PASS line and without touching Docker" {
  run env VERIFY_SLOT=0 "$BASH32" "$SCRIPT" --execute verify
  [ "$status" -ne 0 ]
  [[ "$output" != *"VERIFY PASS"* ]]
  [[ "$output" == *"slot 0 is the user's default stack"* ]]
  assert_untouched
}

@test "VERIFY_SLOT must be a number, rejected with exit 2" {
  run env VERIFY_SLOT=seven "$BASH32" "$SCRIPT" --dry-run launch
  [ "$status" -eq 2 ]
  [[ "$output" == *"VERIFY_SLOT must be a non-negative integer"* ]]
  assert_no_writes
}

@test "an unknown stack is rejected with exit 2 and touches nothing" {
  run "$BASH32" "$SCRIPT" --execute launch bogus
  [ "$status" -eq 2 ]
  [[ "$output" == *"unknown stack bogus"* ]]
  assert_no_writes
  assert_untouched
}

@test "a launch plan names the compose target and the slot port" {
  run "$BASH32" "$SCRIPT" --dry-run launch gateway
  [ "$status" -eq 0 ]
  [[ "$output" == *"INFO dry-run: STACK_SLOT=7 make up-gateway"* ]]
  [[ "$output" == *"http://127.0.0.1:8700/apim/health"* ]]
  assert_no_writes
}

@test "--dry-run verify names the stack and every assertion" {
  run "$BASH32" "$SCRIPT" --dry-run verify
  [ "$status" -eq 0 ]
  [[ "$output" == *"make up-hello-subscription"* ]]
  [[ "$output" == *"expect 401"* ]]
  [[ "$output" == *"expect 200 and x-hello-policy: applied"* ]]
  [[ "$output" == *"smoke expect exit 0"* ]]
}

@test "the default evidence root is .run/verify under the repo" {
  run env -u VERIFY_BASE_DIR -u VERIFY_EVIDENCE_ROOT -u VERIFY_RUN_ID "$BASH32" "$SCRIPT" --dry-run verify
  [ "$status" -eq 0 ]
  [[ "$output" == *"/.run/verify/"* ]]
  [[ "$output" == *"/evidence"* ]]
}

@test "request without a method and path is rejected with exit 2" {
  run "$BASH32" "$SCRIPT" --dry-run request GET
  [ "$status" -eq 2 ]
  assert_no_writes
}

@test "doctor refuses a run that was never launched, with a FAIL line" {
  run "$BASH32" "$SCRIPT" --execute doctor
  [ "$status" -ne 0 ]
  [[ "$output" == *"FAIL: no launched state for run=app-run"* ]]
  assert_untouched
}

@test "cli refuses a hello stack before touching uv" {
  write_state hello-subscription
  run "$BASH32" "$SCRIPT" --execute cli status
  [ "$status" -eq 2 ]
  [[ "$output" == *"management API needs tenant_access"* ]]
  assert_untouched
}

@test "smoke refuses the gateway stack, which has no repo smoke, before touching uv" {
  write_state gateway
  run "$BASH32" "$SCRIPT" --execute smoke
  [ "$status" -eq 2 ]
  [[ "$output" == *"no repo smoke for stack gateway"* ]]
  assert_untouched
}

@test "unexpected arguments to doctor are rejected with exit 2" {
  run "$BASH32" "$SCRIPT" --dry-run doctor extra
  [ "$status" -eq 2 ]
  [[ "$output" == *"unexpected argument: extra"* ]]
}
