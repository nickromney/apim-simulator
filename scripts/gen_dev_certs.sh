#!/usr/bin/env sh
set -eu

DEFAULT_ROOT_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
ROOT_DIR="${APIM_SIMULATOR_ROOT_DIR:-$DEFAULT_ROOT_DIR}"
CERT_DIR="$ROOT_DIR/examples/edge/certs"
APIM_EDGE_ROOT_HOST="${APIM_EDGE_ROOT_HOST:-apim.127.0.0.1.sslip.io}"
APIM_EDGE_HOST="${APIM_EDGE_HOST:-edge.apim.127.0.0.1.sslip.io}"
APIM_EDGE_WILDCARD_HOST="${APIM_EDGE_WILDCARD_HOST:-*.apim.127.0.0.1.sslip.io}"
CERT_PATH="$CERT_DIR/$APIM_EDGE_HOST.crt"
KEY_PATH="$CERT_DIR/$APIM_EDGE_HOST.key"
CA_CERT_PATH="$CERT_DIR/dev-root-ca.crt"
CA_KEY_PATH="$CERT_DIR/dev-root-ca.key"
CA_SERIAL_PATH="$CERT_DIR/dev-root-ca.srl"
LEGACY_CERT_PATH="$CERT_DIR/apim.localtest.me.crt"
LEGACY_KEY_PATH="$CERT_DIR/apim.localtest.me.key"
LEGACY_CSR_PATH="$CERT_DIR/apim.localtest.me.csr"
CERT_CSR_PATH="$CERT_DIR/$APIM_EDGE_HOST.csr"

mkdir -p "$CERT_DIR"

if ! command -v mkcert >/dev/null 2>&1; then
  echo "mkcert is required but was not found in PATH." >&2
  echo "Install it, then run 'mkcert -install' and try again." >&2
  exit 1
fi

CAROOT="$(mkcert -CAROOT 2>/dev/null || true)"
if [ -z "$CAROOT" ] || [ ! -f "$CAROOT/rootCA.pem" ] || [ ! -f "$CAROOT/rootCA-key.pem" ]; then
  echo "mkcert is installed but its local CA is not ready." >&2
  echo "Run 'mkcert -install' and try again." >&2
  if [ "$(uname -s 2>/dev/null || printf '')" = "Darwin" ]; then
    echo "On macOS, mkcert installs into your Keychain trust store." >&2
  else
    echo "On non-macOS hosts, make sure your trust store accepts the mkcert CA." >&2
  fi
  exit 1
fi

rm -f \
  "$LEGACY_CERT_PATH" \
  "$LEGACY_KEY_PATH" \
  "$LEGACY_CSR_PATH" \
  "$CA_KEY_PATH" \
  "$CA_SERIAL_PATH" \
  "$CERT_CSR_PATH"

cp "$CAROOT/rootCA.pem" "$CA_CERT_PATH"

mkcert \
  -cert-file "$CERT_PATH" \
  -key-file "$KEY_PATH" \
  "$APIM_EDGE_ROOT_HOST" \
  "$APIM_EDGE_HOST" \
  "$APIM_EDGE_WILDCARD_HOST" \
  localhost \
  127.0.0.1 >/dev/null 2>&1

# The edge proxy runs as a non-root numeric UID. On Linux bind mounts, a 0600
# key owned by the host user is unreadable inside the container, so keep the
# generated dev server cert and key world-readable. These files are ignored and
# used only for the local self-signed edge stack, not as production secrets.
chmod 644 "$CERT_PATH" "$KEY_PATH" "$CA_CERT_PATH"

printf 'Generated %s and %s\n' "$CERT_PATH" "$KEY_PATH"
printf 'Local CA available at %s\n' "$CA_CERT_PATH"
