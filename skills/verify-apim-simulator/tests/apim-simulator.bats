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

# A live docker and curl for the read-only and cleanup paths. docker records every call in
# docker.log; the FAKE_* variables choose each answer. Nothing reaches a real daemon or gateway.
write_live_stubs() {
  local live="$BATS_TEST_TMPDIR/live"
  mkdir -p "$live"
  cat > "$live/docker" <<'STUB'
#!/bin/bash
printf '%s\n' "$*" >> "$BATS_TEST_TMPDIR/docker.log"
case "$*" in
  *"ps --status running"*) [ -n "${FAKE_PS_EXIT-}" ] && exit "$FAKE_PS_EXIT"; printf '%s\n' "${FAKE_SERVICES-apim-simulator}"; exit 0 ;;
  "ps --filter"*) printf '%s\n' "${FAKE_OWNER-}"; exit 0 ;;
  *" logs "*) exit "${FAKE_LOGS_EXIT:-0}" ;;
  *" down "*) exit "${FAKE_DOWN_EXIT:-0}" ;;
esac
exit 0
STUB
  cat > "$live/curl" <<'STUB'
#!/bin/bash
[ -n "${FAKE_CURL_EXIT-}" ] && exit "$FAKE_CURL_EXIT"
prev=""
for a in "$@"; do
  case "$prev" in
    -o) printf 'hello, verify\n' > "$a" ;;
    -D) printf 'x-hello-policy: applied\n' > "$a" ;;
  esac
  prev="$a"
done
case " $* " in
  *" -w "*) printf '%s' "${FAKE_HTTP-200}" ;;
  *) printf '%s\n' "${FAKE_HEALTH-healthy}" ;;
esac
STUB
  chmod +x "$live/docker" "$live/curl"
  export PATH="$live:$PATH"
}

@test "doctor passes when our project's gateway owns the port and answers health" {
  write_state hello-subscription
  mkdir -p "$VERIFY_EVIDENCE_ROOT/app-run/evidence"
  printf 'abc123\n' > "$VERIFY_EVIDENCE_ROOT/app-run/evidence/revision.txt"
  write_live_stubs
  run env FAKE_OWNER=apim-simulator-hello-s7-apim-simulator-1 FAKE_HEALTH=healthy "$BASH32" "$SCRIPT" --execute doctor
  [ "$status" -eq 0 ]
  [[ "$output" == *"OK services=apim-simulator, health=healthy"* ]]
  [[ "$output" == *"revision abc123 (now "* ]]
}

@test "doctor fails when our apim-simulator service is not running, including when ps errors" {
  # Equivalent mutants: "return N" after vk_fail in cmd_doctor, cmd_cleanup and record_request is unreachable under set -e (vk_fail returns 1 and exits first), so the status is the same for return 0 and return 1.
  write_state hello-subscription
  write_live_stubs
  run env FAKE_SERVICES=other-service "$BASH32" "$SCRIPT" --execute doctor
  [ "$status" -eq 1 ]
  [[ "$output" == *"FAIL: apim-simulator not running in apim-simulator-hello-s7"* ]]
  run env FAKE_PS_EXIT=1 "$BASH32" "$SCRIPT" --execute doctor
  [ "$status" -eq 1 ]
  [[ "$output" == *"FAIL: apim-simulator not running in apim-simulator-hello-s7"* ]]
}

@test "doctor fails when another container publishes the port" {
  write_state hello-subscription
  write_live_stubs
  run env FAKE_OWNER=intruder-1 "$BASH32" "$SCRIPT" --execute doctor
  [ "$status" -eq 1 ]
  [[ "$output" == *"FAIL: port 8700 published by 'intruder-1'"* ]]
  [[ "$output" != *"OK services="* ]]
}

@test "doctor fails when nothing publishes the port" {
  write_state hello-subscription
  write_live_stubs
  run env FAKE_OWNER= "$BASH32" "$SCRIPT" --execute doctor
  [ "$status" -eq 1 ]
  [[ "$output" == *"FAIL: port 8700 published by 'nothing'"* ]]
}

@test "doctor fails when /apim/health cannot be reached" {
  write_state hello-subscription
  write_live_stubs
  run env FAKE_OWNER=apim-simulator-hello-s7-apim-simulator-1 FAKE_CURL_EXIT=7 "$BASH32" "$SCRIPT" --execute doctor
  [ "$status" -eq 1 ]
  [[ "$output" == *"FAIL: /apim/health unreachable"* ]]
}

@test "request records one exchange with its status and evidence files" {
  write_live_stubs
  run "$BASH32" "$SCRIPT" --execute request GET /api/health
  [ "$status" -eq 0 ]
  [[ "$output" == *"status=200"* ]]
  local out
  out="$(ls -d "$VERIFY_EVIDENCE_ROOT"/app-run/evidence/*-request)"
  [ "$(cat "$out/status.txt")" = 200 ]
  [ "$(head -1 "$out/request.txt")" = "GET http://127.0.0.1:8700/api/health" ]
  [ -f "$out/response-headers.txt" ]
}

@test "request with a method but no path is rejected with exit 2 and records nothing" {
  write_live_stubs
  run "$BASH32" "$SCRIPT" --execute request GET
  [ "$status" -eq 2 ]
  [[ "$output" == *"request needs <METHOD> <path>"* ]]
  [ ! -e "$VERIFY_EVIDENCE_ROOT/app-run/evidence" ]
}

@test "request reports a failed curl instead of a status" {
  write_live_stubs
  run env FAKE_CURL_EXIT=7 "$BASH32" "$SCRIPT" --execute request GET /api/health
  [ "$status" -eq 1 ]
  [[ "$output" == *"FAIL: request failed: GET http://127.0.0.1:8700/api/health"* ]]
  [[ "$output" != *"status="* ]]
}

@test "cleanup downs only our project, keeps evidence and removes the run's state" {
  write_state hello-subscription
  mkdir -p "$VERIFY_EVIDENCE_ROOT/app-run/evidence"
  write_live_stubs
  run "$BASH32" "$SCRIPT" --execute cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"stopped run=app-run project=apim-simulator-hello-s7"* ]]
  grep -qx "compose -p apim-simulator-hello-s7 down --remove-orphans" "$BATS_TEST_TMPDIR/docker.log"
  [ -f "$VERIFY_EVIDENCE_ROOT/app-run/evidence/compose.log" ]
  [ ! -e "$VERIFY_BASE_DIR/app-run/state" ]
}

@test "cleanup still runs compose down when compose logs fails" {
  write_state gateway
  mkdir -p "$VERIFY_EVIDENCE_ROOT/app-run/evidence"
  write_live_stubs
  run env FAKE_LOGS_EXIT=1 "$BASH32" "$SCRIPT" --execute cleanup
  [ "$status" -eq 0 ]
  grep -qx "compose -p apim-simulator-core-s7 down --remove-orphans" "$BATS_TEST_TMPDIR/docker.log"
  [ ! -e "$VERIFY_BASE_DIR/app-run/state" ]
}

@test "cleanup reports a failed compose down instead of claiming it stopped" {
  write_state hello-subscription
  mkdir -p "$VERIFY_EVIDENCE_ROOT/app-run/evidence"
  write_live_stubs
  run env FAKE_DOWN_EXIT=1 "$BASH32" "$SCRIPT" --execute cleanup
  [ "$status" -ne 0 ]
  [[ "$output" != *"stopped run=app-run"* ]]
}

@test "cleanup without launched state never calls docker" {
  write_live_stubs
  run "$BASH32" "$SCRIPT" --execute cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"stopped run=app-run project=apim-simulator-hello-s7"* ]]
  [ ! -e "$BATS_TEST_TMPDIR/docker.log" ]
}

@test "the project name follows the stack: hello stacks share one name, gateway has its own" {
  write_state hello 4
  run "$BASH32" "$SCRIPT" --dry-run cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"docker compose -p apim-simulator-hello-s4 logs"* ]]
  write_state gateway 4
  run "$BASH32" "$SCRIPT" --dry-run cleanup
  [ "$status" -eq 0 ]
  [[ "$output" == *"docker compose -p apim-simulator-core-s4 logs"* ]]
}

@test "smoke and cleanup reject extra arguments with exit 2" {
  run "$BASH32" "$SCRIPT" --dry-run cleanup extra
  [ "$status" -eq 2 ]
  [[ "$output" == *"unexpected argument: extra"* ]]
  run "$BASH32" "$SCRIPT" --dry-run smoke extra
  [ "$status" -eq 2 ]
  [[ "$output" == *"unexpected argument: extra"* ]]
}
