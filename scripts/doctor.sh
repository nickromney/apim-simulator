#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# shellcheck source=/dev/null
source "${SCRIPT_DIR}/lib/shell-cli.sh"

# shellcheck disable=SC2329 # invoked by name through the shell_cli_* helpers
usage() {
  cat <<'EOF'
Usage: ./scripts/doctor.sh [--dry-run] [--execute]

Checks this machine can run the simulator and says how to fix anything that
is missing. Runs every check, even after one fails.

FAIL means `make up` will not work until it is fixed.
WARN means some stacks, smoke tests or developer commands will not work.

Examples:
  ./scripts/doctor.sh --execute
  make doctor

Options:
  --dry-run  List the checks and exit
  --execute  Run the checks
  -h, --help Show this message
EOF
}

failures=0
warnings=0

ok() { printf 'OK   %s\n' "$1"; }
warn() {
  warnings=$((warnings + 1))
  printf 'WARN %s\n' "$1"
  [[ -z "${2:-}" ]] || printf '     fix: %s\n' "$2"
}
fail() {
  failures=$((failures + 1))
  printf 'FAIL %s\n' "$1"
  [[ -z "${2:-}" ]] || printf '     fix: %s\n' "$2"
}
have() { command -v "$1" >/dev/null 2>&1; }

# The value a .env file sets for a variable, or nothing. Compose reads .env, so
# this is what `make up` will use.
ENV_FILE="${DOCTOR_ENV_FILE:-${REPO_ROOT}/.env}"
env_file_value() {
  local name="$1"
  [[ -f "${ENV_FILE}" ]] || return 0
  sed -n "s/^[[:space:]]*${name}=//p" "${ENV_FILE}" | tail -n 1
}

check_docker() {
  local engine major

  if ! have docker; then
    fail "docker is not installed" "install Docker Desktop (or Docker Engine 25+): https://docs.docker.com/get-docker/"
    return 1
  fi
  if ! docker compose version >/dev/null 2>&1; then
    fail "the docker compose plugin is not available" "install Docker Compose v2: https://docs.docker.com/compose/install/"
  else
    ok "docker compose $(docker compose version --short 2>/dev/null)"
  fi
  if ! docker info >/dev/null 2>&1; then
    fail "the Docker daemon is not running" "start Docker Desktop, then run make doctor again"
    return 1
  fi

  engine="$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)"
  major="${engine%%.*}"
  if [[ "${major}" =~ ^[0-9]+$ ]] && [[ "${major}" -ge 25 ]]; then
    ok "Docker Engine ${engine}"
  else
    fail "Docker Engine 25 or later is required (found ${engine:-unknown}); the healthchecks use start_interval" \
      "update Docker Desktop or Docker Engine"
  fi

  if docker buildx version >/dev/null 2>&1; then
    ok "docker buildx $(docker buildx version 2>/dev/null | awk '{print $2}')"
  else
    fail "docker buildx is not available; the Dockerfiles need BuildKit" "install the buildx plugin: https://docs.docker.com/build/install-buildx/"
  fi
}

check_image_source() {
  local runtime_image origin=".env"
  # Compose prefers a variable set in the shell over the same one in .env.
  if [[ -n "${PYTHON_RUNTIME_IMAGE:-}" ]]; then
    runtime_image="${PYTHON_RUNTIME_IMAGE}"
    origin="PYTHON_RUNTIME_IMAGE"
  else
    runtime_image="$(env_file_value PYTHON_RUNTIME_IMAGE)"
  fi

  if [[ -n "${runtime_image}" && "${runtime_image}" != dhi.io/* ]]; then
    ok "using stock images from ${origin} (${runtime_image%%@*}); no dhi.io login needed"
    return 0
  fi
  if ! have uv; then
    warn "cannot check the dhi.io login without uv" "install uv (see below), then run make doctor again"
    return 0
  fi
  if uv run --quiet --no-project python "${SCRIPT_DIR}/check_docker_registry_auth.py" \
    dhi.io "Docker Hardened Images (dhi.io)" 2>/dev/null; then
    return 0
  fi
  fail "not logged in to dhi.io, so the default images cannot be pulled" \
    "run: docker login dhi.io   (or use stock images: cp .env.example .env and uncomment the image overrides)"
}

check_uv_and_python() {
  local wanted found

  if ! have uv; then
    warn "uv is not installed; smoke tests, make test and make lint need it" \
      "install uv: https://docs.astral.sh/uv/getting-started/installation/"
    return 0
  fi
  ok "uv $(uv --version | awk '{print $2}')"

  wanted="$(tr -d '[:space:]' <"${REPO_ROOT}/.python-version")"
  if found="$(cd "${REPO_ROOT}" && uv python find --no-python-downloads "${wanted}" 2>/dev/null)"; then
    ok "Python ${wanted} for local tools ($("${found}" -c 'import platform; print(platform.python_version())'))"
  else
    warn "Python ${wanted} is not installed; uv will download it on first use" "or install it now: uv python install ${wanted}"
  fi
}

check_mkcert() {
  local caroot

  if ! have mkcert; then
    warn "mkcert is not installed; the TLS, edge and OpenTelemetry stacks need it" \
      "install mkcert (brew install mkcert), then run: mkcert -install"
    return 0
  fi
  caroot="$(mkcert -CAROOT 2>/dev/null || true)"
  if [[ -n "${caroot}" && -f "${caroot}/rootCA.pem" && -f "${caroot}/rootCA-key.pem" ]]; then
    ok "mkcert local CA is ready"
  else
    warn "mkcert is installed but its local CA is not ready" "run: mkcert -install"
  fi
}

# check_ports fail|warn DESCRIPTION PORT...
check_ports() {
  local level="$1" what="$2" busy
  shift 2

  if ! have lsof && ! have ss; then
    warn "cannot check host ports: neither lsof nor ss is installed"
    return 0
  fi
  # check-host-ports.sh exits 1 when a port is busy; that is a finding, not an error.
  busy="$({ "${SCRIPT_DIR}/check-host-ports.sh" --execute "$@" 2>&1 || true; } |
    sed -n 's/^FAIL host port \([0-9]*\) .*/\1/p' | tr '\n' ' ')"
  if [[ -z "${busy}" ]]; then
    ok "${what} host ports are free ($*)"
  else
    "${level}" "${what} host ports already in use: ${busy% }" \
      "stop whatever is listening (make down, if it is an earlier stack), or see: make help-stacks"
  fi
}

check_node() {
  local wanted version

  wanted="$(sed -n 's/^[[:space:]]*NODE_VERSION:[[:space:]]*"\{0,1\}\([0-9]*\).*/\1/p' "${REPO_ROOT}/.github/workflows/ci.yml" | head -n 1)"
  if ! have node; then
    warn "node is not installed; only make frontend-check needs it" "install Node.js ${wanted}"
    return 0
  fi
  version="$(node --version)"
  version="${version#v}"
  if [[ "${version%%.*}" == "${wanted}" ]]; then
    ok "node ${version}"
  else
    warn "node ${version} found; CI uses Node.js ${wanted}, so make frontend-check may differ from CI" \
      "install Node.js ${wanted}"
  fi
}

shell_cli_init_standard_flags
while [[ $# -gt 0 ]]; do
  if shell_cli_handle_standard_flag usage "$1"; then
    shift
    continue
  fi
  shell_cli_unknown_flag "$(shell_cli_script_name)" "$1"
  exit 1
done

shell_cli_maybe_execute_or_preview_summary usage \
  "would check Docker, buildx, the dhi.io login, host ports, uv and Python, mkcert and node"

printf 'Required for make up:\n'
if check_docker; then
  check_image_source
fi
check_ports fail "make up" 8000 3007
printf '\nOptional, for some stacks and developer commands:\n'
check_uv_and_python
check_mkcert
check_ports warn "other stacks'" 3000 8443 8088 8180 9443
check_node

printf '\n'
if [[ "${failures}" -gt 0 ]]; then
  printf '%d problem(s) to fix before make up will work, %d warning(s).\n' "${failures}" "${warnings}"
  exit 1
fi
if [[ "${warnings}" -gt 0 ]]; then
  printf 'Ready for make up, with %d warning(s).\n' "${warnings}"
else
  printf 'Ready for make up.\n'
fi
