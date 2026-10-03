"""Reproduce the pinned browser asset; review manifest changes before updating."""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import tarfile
import urllib.request
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "app/static/scalar"


def main() -> None:
    manifest = json.loads((ASSETS / "manifest.json").read_text())
    expected_url = f"https://registry.npmjs.org/@scalar/api-reference/-/api-reference-{manifest['version']}.tgz"
    if manifest["tarball"] != expected_url:
        raise SystemExit("Manifest tarball must be the pinned official npm release")
    with urllib.request.urlopen(expected_url, timeout=30) as response:
        tarball = response.read()
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(tarball).digest()).decode()
    if integrity != manifest["integrity"]:
        raise SystemExit("Scalar npm tarball integrity mismatch")
    with tarfile.open(fileobj=io.BytesIO(tarball)) as archive:
        member = archive.extractfile("package/dist/browser/standalone.js")
        if member is None:
            raise SystemExit("Scalar browser bundle is missing")
        bundle = member.read()
    if hashlib.sha256(bundle).hexdigest() != manifest["sha256"]:
        raise SystemExit("Scalar browser bundle checksum mismatch")
    (ASSETS / "scalar.js.gz").write_bytes(gzip.compress(bundle, mtime=0))
    print(f"Vendored Scalar {manifest['version']}: {len(bundle):,} bytes before gzip")


if __name__ == "__main__":
    main()
