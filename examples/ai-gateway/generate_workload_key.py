"""Generate the throwaway RSA key the keyless AI gateway signs managed identity tokens with.

The key never leaves this machine: its directory is gitignored and excluded
from the Docker build context, and the gateway reads it from a read-only
mount. The gateway publishes only the public half, at
/.well-known/workload-identity/jwks.json. Running the script again keeps an
existing key, so tokens stay verifiable across restarts.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

KEY_FILENAME = "private.pem"


def ensure_key(directory: Path) -> tuple[Path, bool]:
    """Return the key path and whether this call created it."""
    path = directory / KEY_FILENAME
    if path.exists():
        return path, False
    directory.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    # The gateway runs as a fixed nonroot UID that does not own bind-mounted
    # host files, so this local-only key must be world-readable, as the edge
    # dev certificates are.
    directory.chmod(0o755)
    path.chmod(0o644)
    return path, True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    path, created = ensure_key(args.directory)
    print(f"{'generated' if created else 'kept existing'} workload identity key: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
