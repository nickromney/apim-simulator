#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./scripts/check-host-ports.sh [PORT...]

Checks whether each host TCP port is free for listening.

Examples:
  ./scripts/check-host-ports.sh
  ./scripts/check-host-ports.sh 3000 8000 9443

Defaults:
  3000 3001 3007 8000 8088 8180 9443
EOF
}

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

listeners_for_port_lsof() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
}

listeners_for_port_ss() {
  local port="$1"
  local body

  body="$(ss -H -ltn "sport = :${port}" 2>/dev/null || true)"
  [[ -n "$body" ]] || return 0
  printf 'State Recv-Q Send-Q Local Address:Port Peer Address:Port\n%s\n' "$body"
}

listeners_for_port() {
  local port="$1"

  if have_cmd lsof; then
    listeners_for_port_lsof "$port"
    return 0
  fi

  if have_cmd ss; then
    listeners_for_port_ss "$port"
    return 0
  fi

  echo "Neither lsof nor ss is available; cannot inspect host ports." >&2
  exit 2
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if [[ "$#" -eq 0 ]]; then
  set -- 3000 3001 3007 8000 8088 8180 9443
fi

conflicts=0

for port in "$@"; do
  if ! [[ "$port" =~ ^[0-9]+$ ]]; then
    echo "invalid port: $port" >&2
    exit 2
  fi

  listeners="$(listeners_for_port "$port")"
  if [[ -z "$listeners" ]]; then
    echo "OK   host port $port is free"
    continue
  fi

  conflicts=1
  echo "FAIL host port $port is already in use" >&2
  printf '%s\n' "$listeners" >&2
done

exit "$conflicts"
