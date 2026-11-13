from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx


def validate_receipt_path(path: str) -> None:
    output = Path(path)
    if os.path.lexists(output):
        raise ValueError("receipt path already exists")
    if not output.parent.is_dir():
        raise ValueError("receipt parent directory does not exist")
    if not os.access(output.parent, os.W_OK):
        raise ValueError("receipt parent directory is not writable")


def _digest(value: Any) -> str:
    data = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def write_receipt(path: str, args: Any, response: httpx.Response) -> None:
    body = args._bundle_request_body
    url = httpx.URL(args.base_url).copy_with(username=None, password=None, query=None, fragment=None)
    receipt = {
        "schema_version": 1,
        "target_url": str(url),
        "applied_at": datetime.now(UTC).isoformat(),
        "desired_sha256": _digest(body["artifact"]),
        "prune": bool(args.prune),
        "reviewed": bool(args.reviewed),
        "etag": response.headers.get("etag"),
    }
    if "baseline" in body:
        receipt["baseline_sha256"] = _digest(body["baseline"])
    if args._git_inputs:
        receipt["git_inputs"] = {
            name: {key: item[key] for key in ("commit", "path")} for name, item in args._git_inputs.items()
        }
    output = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(receipt, stream, indent=2)
            stream.write("\n")
        os.link(temporary, output)
    finally:
        os.unlink(temporary)
