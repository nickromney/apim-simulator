#!/usr/bin/env python3
"""How the smoke and verify scripts decide what TLS trust to use.

This lives on its own because it used to live in `smoke_mcp.py`, which imports
the MCP SDK at module scope. Four scripts wanted only this one function, so an
OTEL check could not run without the `mcp` extra installed and failed with
`ModuleNotFoundError: No module named 'mcp'`.
"""

from __future__ import annotations

import os
from pathlib import Path


def resolve_tls_verify(
    *,
    default_ca: Path | None = None,
    ca_env: str,
    verify_env: str,
    insecure_env: str,
) -> bool | str:
    """Resolve httpx's `verify` argument from the environment.

    Precedence, highest first: an explicit insecure opt-out, an explicit CA
    bundle, the legacy verify variable (which may name a bundle or say false),
    then the stack's own dev CA if it is on disk, then system trust.
    """
    if os.getenv(insecure_env, "false").lower() == "true":
        return False

    explicit_ca = os.getenv(ca_env, "").strip()
    if explicit_ca:
        return explicit_ca

    legacy_verify = os.getenv(verify_env, "").strip()
    if legacy_verify and legacy_verify.lower() not in {"true", "false"}:
        return legacy_verify
    if legacy_verify.lower() == "false":
        return False

    if default_ca is not None and default_ca.exists():
        return str(default_ca)

    return True
