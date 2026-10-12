#!/usr/bin/env bash
# Verification harness for the APIM simulator: an isolated compose slot, driven through published localhost ports.
# Built on verify-kit (scripts/verify-lib.sh). Bash 3.2 compatible (macOS /bin/bash). Run with --help for usage.
# shellcheck disable=SC2034 # VK_* settings are read by the sourced verify-lib.sh
set -euo pipefail

VK_SCRIPT="$0"
VK_APP="apim-simulator"
VK_DESCRIPTION="Verification harness for the APIM simulator: isolated compose slot, driven through the published gateway port."
VK_DEFAULT_BASE_DIR="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)/.run/verify"
# shellcheck source=verify-lib.sh disable=SC1091
. "$(cd "$(dirname "$0")" && pwd)/verify-lib.sh"

vk_commands_help() {
  cat <<'HELP'
  launch [stack]         stack: hello-subscription (default) | hello | gateway
  doctor                 read-only: our compose project is up, the port is ours, /apim/health answers
  request <METHOD> <path> [Header:Value ...]   record one HTTP exchange through the gateway
  smoke                  run scripts/smoke_hello.py for the stack against our port
  cli <apimsim args>     management CLI (gateway stack only; tenant key from examples/basic.json)
  cleanup                compose down for our project only; evidence is kept
HELP
}

vk_env_help() {
  cat <<'HELP'
  VERIFY_SLOT            compose slot (default 7 -> gateway port 8700). Slot 0 is the user's default stack and is refused.
HELP
}

vk_sample_commands() {
  printf '%s\n' verify launch "launch gateway" doctor "request GET /api/health" smoke "cli status" cleanup
}

# Derive paths and the slot/stack/port. Reads state only; creates nothing.
vk_paths() {
  STATE="$VK_BASE_DIR/$VK_RUN_ID/state"
  EVIDENCE="$VK_EVIDENCE_ROOT/$VK_RUN_ID/evidence"
  SLOT="$(cat "$STATE/slot" 2>/dev/null || echo "${VERIFY_SLOT:-7}")"
  case "$SLOT" in
    ''|*[!0-9]*) vk_err "VERIFY_SLOT must be a non-negative integer, got '$SLOT'"; exit 2 ;;
  esac
  if [ "$VK_CMD" = verify ]; then
    STACK=hello-subscription
  else
    STACK="$(cat "$STATE/stack" 2>/dev/null || echo hello-subscription)"
  fi
  vk_set_stack "$STACK" || exit 2
  cd "$VK_ROOT"
}

vk_set_stack() {
  case "$1" in
    hello|hello-subscription|gateway) STACK="$1" ;;
    *) vk_err "unknown stack $1"; return 2 ;;
  esac
  PORT=$((8000 + SLOT * 100))
  URL="http://127.0.0.1:$PORT"
}

vk_no_args() { [ $# -eq 0 ] || { vk_err "unexpected argument: $1"; return 2; }; }

project() {
  case "$STACK" in
    hello|hello-subscription) echo "apim-simulator-hello-s$SLOT" ;;
    gateway) echo "apim-simulator-core-s$SLOT" ;;
  esac
}
running() { docker compose -p "$(project)" ps --status running --services 2>/dev/null; }

vk_plan() {
  local c="$1"
  shift
  case "$c" in
    verify)
      vk_no_args "$@"
      vk_plan_line "launch hello-subscription on slot $SLOT (STACK_SLOT=$SLOT make up-hello-subscription); doctor; GET /api/health expect 401; GET /api/hello?name=verify with the primary key expect 200 and x-hello-policy: applied; smoke expect exit 0; cleanup. Evidence in $EVIDENCE" ;;
    launch)
      [ $# -le 1 ] || { vk_err "unexpected argument: $2"; return 2; }
      vk_set_stack "${1:-hello-subscription}"
      vk_plan_line "STACK_SLOT=$SLOT make up-$STACK, then wait up to 60s for $URL/apim/health; state in $STATE; evidence in $EVIDENCE" ;;
    doctor)
      vk_no_args "$@"
      vk_plan_line "read-only: docker compose -p $(project) ps, owner of port $PORT, GET $URL/apim/health" ;;
    request)
      [ $# -ge 2 ] || { vk_err "request needs <METHOD> <path>"; return 2; }
      vk_plan_line "record $* through $URL into $EVIDENCE" ;;
    smoke)
      vk_no_args "$@"
      vk_plan_line "run scripts/smoke_hello.py for stack $STACK against $URL; evidence in $EVIDENCE" ;;
    cli)
      vk_plan_line "run apimsim $* against $URL (gateway stack only); evidence in $EVIDENCE" ;;
    cleanup)
      vk_no_args "$@"
      vk_plan_line "docker compose -p $(project) logs, then down --remove-orphans; remove $STATE; keep $EVIDENCE" ;;
    *) vk_unknown_command ;;
  esac
}

vk_dispatch() {
  local c="$1"
  shift
  case "$c" in
    launch)
      [ $# -le 1 ] || { vk_err "unexpected argument: $2"; return 2; }
      vk_set_stack "${1:-hello-subscription}"
      cmd_launch ;;
    doctor) vk_no_args "$@"; cmd_doctor ;;
    request)
      [ $# -ge 2 ] || { vk_err "request needs <METHOD> <path>"; return 2; }
      cmd_request "$@" ;;
    smoke) vk_no_args "$@"; cmd_smoke ;;
    cli) cmd_cli "$@" ;;
    cleanup) vk_no_args "$@"; cmd_cleanup ;;
    *) vk_unknown_command ;;
  esac
}

cmd_launch() {
  [ "$SLOT" != 0 ] || { vk_fail "slot 0 is the user's default stack; pick VERIFY_SLOT>=1"; return 1; }
  if vk_port_busy "$PORT"; then
    vk_fail "port $PORT in use (another slot-$SLOT stack?); set VERIFY_SLOT"; return 1
  fi
  mkdir -p "$STATE" "$EVIDENCE"
  vk_mark_current
  echo "$SLOT" > "$STATE/slot"; echo "$STACK" > "$STATE/stack"
  vk_record_revision "$EVIDENCE/revision.txt"
  # APIM_GATEWAY_PORT is passed explicitly: under `make verify` the outer make exports its own slot-0 value,
  # and the Makefile's `APIM_GATEWAY_PORT ?=` would keep it and publish on 8000.
  APIM_GATEWAY_PORT="$PORT" STACK_SLOT="$SLOT" make "up-$STACK" > "$EVIDENCE/launch.log" 2>&1 \
    || { vk_fail "make up-$STACK failed; see $EVIDENCE/launch.log"; return 1; }
  local _ code
  for _ in $(seq 1 60); do
    # Each probe's code and curl stderr go to evidence, so a timeout says why.
    code="$(curl -sS -o /dev/null -w '%{http_code}' "$URL/apim/health" 2>>"$EVIDENCE/health-probe.log" || true)"
    if [ "$code" = 200 ]; then
      vk_say "ready run=$VK_RUN_ID stack=$STACK project=$(project) url=$URL"; return 0
    fi
    sleep 1
  done
  vk_fail "gateway not healthy after 60s (last probe code=${code:-none}; see $EVIDENCE/health-probe.log); docker compose -p $(project) logs"
}

cmd_doctor() {
  vk_say "run=$VK_RUN_ID stack=$STACK project=$(project) url=$URL"
  [ -d "$STATE" ] || { vk_fail "no launched state for run=$VK_RUN_ID; launch first"; return 1; }
  local services owner body
  services="$(running || true)"
  grep -qx apim-simulator <<<"$services" || { vk_fail "apim-simulator not running in $(project)"; return 1; }
  owner="$(docker ps --filter "publish=$PORT" --format '{{.Names}}' | head -1)"
  [[ "$owner" == "$(project)-apim-simulator-1" ]] || { vk_fail "port $PORT published by '${owner:-nothing}'"; return 1; }
  body="$(curl -fsS "$URL/apim/health")" || { vk_fail "/apim/health unreachable"; return 1; }
  vk_say "revision $(head -1 "$EVIDENCE/revision.txt") (now $(git -C "$VK_ROOT" rev-parse HEAD))"
  vk_say "OK services=$(tr '\n' ',' <<<"$services") health=$body"
}

# record_request METHOD PATH [Header:Value ...]: one exchange through the gateway, kept as evidence.
# Sets REQ_STATUS and REQ_OUT (the evidence directory).
record_request() {
  local method="$1" path="$2" out h
  local args
  shift 2
  out="$EVIDENCE/$(vk_stamp)-request"; mkdir -p "$out"
  args=(-sS -X "$method" -D "$out/response-headers.txt" -o "$out/response-body.txt" -w '%{http_code}')
  for h in ${@+"$@"}; do args+=(-H "$h"); done
  printf '%s %s\n' "$method" "$URL$path" > "$out/request.txt"
  printf '%s\n' ${@+"$@"} >> "$out/request.txt"
  REQ_STATUS="$(curl "${args[@]}" "$URL$path")" || { vk_fail "request failed: $method $URL$path"; return 1; }
  echo "$REQ_STATUS" > "$out/status.txt"
  REQ_OUT="$out"
}

cmd_request() {
  record_request "$@"
  vk_say "status=$REQ_STATUS"; cat "$REQ_OUT/response-body.txt"; echo
  vk_say "evidence: $REQ_OUT"
}

# run_smoke: the repo smoke script in the stack's mode. Sets SMOKE_EXIT and SMOKE_OUT.
run_smoke() {
  local mode rc
  case "$STACK" in
    hello) mode=anonymous ;;
    hello-subscription) mode=subscription ;;
    *) vk_err "no repo smoke for stack $STACK; use request/cli"; return 2 ;;
  esac
  SMOKE_OUT="$EVIDENCE/$(vk_stamp)-smoke"; mkdir -p "$SMOKE_OUT"
  set +e
  SMOKE_HELLO_MODE="$mode" SMOKE_HELLO_BASE_URL="$URL" uv run --extra dev python scripts/smoke_hello.py \
    > "$SMOKE_OUT/stdout.txt" 2> "$SMOKE_OUT/stderr.txt"
  rc=$?
  set -e
  echo "$rc" > "$SMOKE_OUT/exit-code.txt"
  SMOKE_EXIT="$rc"
}

cmd_smoke() {
  run_smoke
  cat "$SMOKE_OUT/stdout.txt" "$SMOKE_OUT/stderr.txt"
  vk_say "exit=$SMOKE_EXIT evidence: $SMOKE_OUT"
}

cmd_cli() {
  [ "$STACK" = gateway ] || { vk_err "management API needs tenant_access; launch the gateway stack"; return 2; }
  local out rc
  out="$EVIDENCE/$(vk_stamp)-cli"; mkdir -p "$out"
  printf '%q ' apimsim --base-url "$URL" ${@+"$@"} > "$out/command.txt"
  set +e
  uv run --extra dev python -m app.cli --base-url "$URL" --tenant-key local-dev-tenant-key ${@+"$@"} \
    > "$out/stdout.txt" 2> "$out/stderr.txt"
  rc=$?
  set -e
  echo "$rc" > "$out/exit-code.txt"
  cat "$out/stdout.txt" "$out/stderr.txt"; vk_say "exit=$rc evidence: $out"
}

cmd_cleanup() {
  if [ -d "$STATE" ]; then
    docker compose -p "$(project)" logs --no-color > "$EVIDENCE/compose.log" 2>&1 || true
    docker compose -p "$(project)" down --remove-orphans > "$EVIDENCE/down.log" 2>&1
  fi
  rm -rf "${STATE:?}"
  vk_clear_current
  vk_say "stopped run=$VK_RUN_ID project=$(project); evidence kept at $EVIDENCE"
}

vk_verify_recipes() {
  record_request GET /api/health
  vk_expect_equals "GET /api/health without a key: status" "$REQ_STATUS" 401
  vk_expect_contains "$REQ_OUT/response-body.txt" 'missing subscription key'
  record_request GET '/api/hello?name=verify' 'Ocp-Apim-Subscription-Key: hello-demo-key'
  vk_expect_equals "GET /api/hello with the primary key: status" "$REQ_STATUS" 200
  vk_expect_contains "$REQ_OUT/response-body.txt" '"message":"hello, verify"'
  vk_expect_contains "$REQ_OUT/response-headers.txt" 'x-hello-policy: applied'
  run_smoke
  vk_expect_equals "smoke (scripts/smoke_hello.py, subscription mode): exit" "$SMOKE_EXIT" 0
}

vk_main "$@"
