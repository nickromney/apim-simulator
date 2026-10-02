#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
source "$ROOT_DIR/scripts/tutorial_lib.sh"

init_tutorial_env
EXECUTE=0
VERIFY=0
DRY_RUN=0

usage() {
  cat <<EOF
Usage: ./docs/tutorials/apim-get-started/tutorial10.sh [--setup|--execute|--verify|--dry-run]

Runs tutorial step 10 for the APIM simulator.

Flags:
  --setup, --execute  Start the local stack and run the Bruno authoring collection.
  --verify            Run the Bruno collection again against the existing stack.
  --dry-run           Show this help and preview the setup action without side effects.
  --help, -h          Show this help text.
EOF
}

verify_tutorial() {
  echo "Running Bruno CLI: import, edit settings/policies, three calls, throttling, export"
  make -C "$ROOT_DIR/examples/apim-tutorials" bruno \
    APIM_BASE="$APIM_BASE" APIM_TENANT_KEY="$APIM_TENANT_KEY" \
    APIM_UPSTREAM_BASE_URL="$APIM_UPSTREAM_BASE_URL"
}

while (($# > 0)); do
  case "$1" in
    --setup|--execute)
      EXECUTE=1
      ;;
    --verify)
      VERIFY=1
      ;;
    --dry-run)
      DRY_RUN=1
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [[ "$EXECUTE" -eq 1 && "$VERIFY" -eq 1 ]]; then
  echo "Choose either --setup/--execute or --verify." >&2
  usage >&2
  exit 2
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  usage
  echo "INFO dry-run: would run $(basename "$0") setup; use --verify to run the collection against the existing stack"
  exit 0
fi

if [[ "$EXECUTE" -eq 0 && "$VERIFY" -eq 0 ]]; then
  usage
  echo "INFO dry-run: would run $(basename "$0") setup; use --verify to run the collection against the existing stack"
  exit 0
fi

if [[ "$VERIFY" -eq 1 ]]; then
  run_verify_with_setup_hint "./docs/tutorials/apim-get-started/tutorial10.sh" verify_tutorial
  exit 0
fi

echo "Starting tutorial 10 stack with docker compose"
start_public_stack

echo "Waiting for gateway health at $APIM_BASE/apim/health"
wait_for_gateway

verify_tutorial

echo "Setup complete. Run ./docs/tutorials/apim-get-started/tutorial10.sh --verify to repeat the Bruno workflow."
