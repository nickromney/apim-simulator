#!/usr/bin/env bats

# Every tool doctor.sh calls is stubbed, so these tests describe the report a
# learner sees for each machine state without needing Docker running.

setup() {
  export REPO_ROOT
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../.." && pwd)"
  export TEST_BIN="$BATS_TEST_TMPDIR/bin"
  export TEST_CAROOT="$BATS_TEST_TMPDIR/mkcert-caroot"
  export DOCTOR_ENV_FILE="$BATS_TEST_TMPDIR/.env"
  export DOCKER_ENGINE_VERSION="29.1.0"
  export DHI_LOGGED_IN=1
  mkdir -p "$TEST_BIN" "$TEST_CAROOT"
  # PATH holds only these stubs and the core utilities the script uses, so a
  # real docker or node on the host (e.g. /usr/bin on CI runners) cannot leak in.
  local tool
  for tool in bash env sed awk tr tail head cat dirname basename chmod; do
    ln -s "$(command -v "$tool")" "$TEST_BIN/$tool"
  done
  printf 'root-cert' >"$TEST_CAROOT/rootCA.pem"
  printf 'root-key' >"$TEST_CAROOT/rootCA-key.pem"

  stub docker <<'EOF'
case "$1 ${2:-}" in
  "compose version") echo "2.40.0" ;;
  "info "*) exit 0 ;;
  "version --format") echo "$DOCKER_ENGINE_VERSION" ;;
  "buildx version") echo "github.com/docker/buildx v0.30.0 abc" ;;
  *) echo "docker stub: unsupported: $*" >&2; exit 1 ;;
esac
EOF
  stub uv <<'EOF'
case "$1" in
  --version) echo "uv 0.13.7" ;;
  run) [ "$DHI_LOGGED_IN" = 1 ] && echo "OK   Docker Hardened Images (dhi.io) credentials found" ;;
  python) echo "$TEST_BIN/python3.13" ;;
  *) echo "uv stub: unsupported: $*" >&2; exit 1 ;;
esac
EOF
  stub python3.13 <<'EOF'
echo "3.13.18"
EOF
  stub mkcert <<'EOF'
echo "$TEST_CAROOT"
EOF
  stub lsof <<'EOF'
exit 0
EOF
  stub node <<'EOF'
echo "v22.23.4"
EOF
}

stub() {
  { printf '#!/usr/bin/env bash\nset -u\n'; cat; } >"$TEST_BIN/$1"
  chmod +x "$TEST_BIN/$1"
}

run_doctor() {
  run env PATH="$TEST_BIN" "$REPO_ROOT/scripts/doctor.sh" --execute
}

@test "a ready machine passes every check" {
  run_doctor

  [ "$status" -eq 0 ]
  [[ "$output" == *"OK   Docker Engine 29.1.0"* ]]
  [[ "$output" == *"OK   Python 3.13 for local tools (3.13.18)"* ]]
  [[ "$output" == *"Ready for make up."* ]]
  [[ "$output" != *"WARN"* ]]
}

@test "missing docker fails but the optional checks still run" {
  rm "$TEST_BIN/docker"

  run_doctor

  [ "$status" -eq 1 ]
  [[ "$output" == *"FAIL docker is not installed"* ]]
  [[ "$output" == *"fix: install Docker Desktop"* ]]
  [[ "$output" == *"OK   uv 0.13.7"* ]]
  [[ "$output" == *"1 problem(s) to fix before make up will work"* ]]
}

@test "an engine older than 25 fails with the reason" {
  export DOCKER_ENGINE_VERSION="24.0.9"

  run_doctor

  [ "$status" -eq 1 ]
  [[ "$output" == *"FAIL Docker Engine 25 or later is required (found 24.0.9)"* ]]
}

@test "no dhi.io login fails and offers both fixes" {
  export DHI_LOGGED_IN=0

  run_doctor

  [ "$status" -eq 1 ]
  [[ "$output" == *"FAIL not logged in to dhi.io"* ]]
  [[ "$output" == *"docker login dhi.io"* ]]
  [[ "$output" == *"cp .env.example .env"* ]]
}

@test "stock images in .env need no dhi.io login" {
  export DHI_LOGGED_IN=0
  printf 'PYTHON_RUNTIME_IMAGE=python:3.13-slim@sha256:abc\n' >"$DOCTOR_ENV_FILE"

  run_doctor

  [ "$status" -eq 0 ]
  [[ "$output" == *"OK   using stock images from .env (python:3.13-slim)"* ]]
}

@test "stock images set in the shell need no dhi.io login" {
  export DHI_LOGGED_IN=0

  run env PATH="$TEST_BIN" PYTHON_RUNTIME_IMAGE="python:3.13-slim@sha256:abc" "$REPO_ROOT/scripts/doctor.sh" --execute

  [ "$status" -eq 0 ]
  [[ "$output" == *"OK   using stock images from PYTHON_RUNTIME_IMAGE (python:3.13-slim)"* ]]
}

@test "missing optional tools warn without blocking make up" {
  rm "$TEST_BIN/uv" "$TEST_BIN/mkcert" "$TEST_BIN/node"

  run_doctor

  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN uv is not installed"* ]]
  [[ "$output" == *"WARN mkcert is not installed"* ]]
  [[ "$output" == *"WARN node is not installed; only make frontend-check needs it"* ]]
  [[ "$output" == *"Ready for make up, with 4 warning(s)."* ]]
}

@test "a busy make up port fails and the report still finishes" {
  stub lsof <<'EOF'
case "$*" in
  *TCP:8000*) printf 'COMMAND PID USER\npython 42 me\n' ;;
esac
EOF

  run_doctor

  [ "$status" -eq 1 ]
  [[ "$output" == *"FAIL make up host ports already in use: 8000"* ]]
  [[ "$output" == *"OK   node 22.23.4"* ]]
}

@test "a busy port another stack uses only warns" {
  stub lsof <<'EOF'
case "$*" in
  *TCP:9443*) printf 'COMMAND PID USER\npython 42 me\n' ;;
esac
EOF

  run_doctor

  [ "$status" -eq 0 ]
  [[ "$output" == *"OK   make up host ports are free (8000 3007)"* ]]
  [[ "$output" == *"WARN other stacks' host ports already in use: 9443"* ]]
}

@test "a node major other than CI's warns" {
  stub node <<'EOF'
echo "v26.13.0"
EOF

  run_doctor

  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN node 26.13.0 found; CI uses Node.js 22"* ]]
}

@test "without --execute it prints help and runs nothing" {
  run env PATH="$TEST_BIN" "$REPO_ROOT/scripts/doctor.sh"

  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage: ./scripts/doctor.sh"* ]]
  [[ "$output" != *"OK   "* ]]
}
