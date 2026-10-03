from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.config import BackendConfig, GatewayConfig

NAMED_VALUE_PATTERN = re.compile(r"\{\{([^{}]+)\}\}")
ENV_NAME_PATTERN = re.compile(r"[^A-Za-z0-9]+")


@dataclass(frozen=True)
class ResolvedNamedValue:
    name: str
    value: str | None
    is_secret: bool
    source: str
    env_var_name: str


def named_value_env_var(name: str) -> str:
    normalized = ENV_NAME_PATTERN.sub("_", name.strip()).strip("_").upper()
    return f"APIM_NAMED_VALUE_{normalized}" if normalized else "APIM_NAMED_VALUE"


def _find_named_value(config: GatewayConfig, name: str):
    if name in config.named_values:
        return name, config.named_values[name]
    return next(
        ((identifier, entry) for identifier, entry in config.named_values.items() if entry.display_name == name),
        (name, None),
    )


def _vault_http_client(config: GatewayConfig, base: str):
    from app.certificate_security import tls_context
    from app.egress_security import EgressSyncTransport

    backend = BackendConfig(
        url=base,
        ca_file=os.getenv("APIM_LOCAL_VAULT_CA_FILE"),
        crl_file=os.getenv("APIM_LOCAL_VAULT_CRL_FILE"),
        client_certificate_file=os.getenv("APIM_LOCAL_VAULT_CLIENT_CERT_FILE"),
        client_certificate_key_file=os.getenv("APIM_LOCAL_VAULT_CLIENT_KEY_FILE"),
    )
    context = tls_context(backend)
    transport = EgressSyncTransport(httpx.HTTPTransport(verify=context), lambda: config)
    return httpx.Client(timeout=2, transport=transport, trust_env=False, follow_redirects=False)


def _vault_authorization(config: GatewayConfig, identity_client_id: str | None):
    resource = os.getenv("APIM_LOCAL_VAULT_RESOURCE")
    if config.workload_identity.mode == "demo":
        return "Bearer " + os.environ.get("APIM_LOCAL_VAULT_KEY", "")
    if not resource:
        raise ValueError("Signed local vault authentication requires an audience resource")
    from app.workload_identity import issue_workload_token

    return "Bearer " + issue_workload_token(config.workload_identity, resource, identity_client_id)


def _local_vault_value(
    secret_id: str, config: GatewayConfig, identity_client_id: str | None = None, *, value_limit: int = 4096
) -> str | None:
    base = os.environ.get("APIM_LOCAL_VAULT_BASE_URL", "").rstrip("/")
    if not base:
        return None
    parts, source = urlsplit(base), urlsplit(secret_id)
    if parts.scheme not in {"http", "https"} or not parts.hostname or source.scheme not in {"http", "https"}:
        raise ValueError("Invalid local vault URL mapping")
    if not re.fullmatch(r"/secrets/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)?", source.path):
        raise ValueError("Invalid named value secret identifier path")
    authorization = _vault_authorization(config, identity_client_id)
    try:
        with _vault_http_client(config, base) as client:
            with client.stream("GET", base + source.path, headers={"Authorization": authorization}) as response:
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > max(32768, value_limit + 1024):
                        raise ValueError("Local vault response exceeds 32 KB")
        value = json.loads(data)["value"]
        if not isinstance(value, str) or not 1 <= len(value) <= value_limit:
            raise ValueError(f"Local vault secret must contain 1 to {value_limit} characters")
        return value
    except (httpx.HTTPError, KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Unable to resolve local vault secret") from exc


def rename_named_value_references(config: GatewayConfig, old: str, new: str) -> GatewayConfig:
    pattern = re.compile(r"\{\{\s*" + re.escape(old) + r"\s*\}\}")

    def rewrite(value):
        if isinstance(value, str):
            return pattern.sub("{{" + new + "}}", value)
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        return value

    payload = config.model_dump(mode="json")
    return GatewayConfig.model_validate(
        {key: value if key == "named_values" else rewrite(value) for key, value in payload.items()}
    )


_request_resolutions: ContextVar[dict | None] = ContextVar("named_value_resolutions", default=None)


@contextmanager
def named_value_resolution_scope():
    token = _request_resolutions.set({})
    try:
        yield
    finally:
        _request_resolutions.reset(token)


def _resolve_named_value(config: GatewayConfig, name: str) -> ResolvedNamedValue | None:
    identifier, entry = _find_named_value(config, name)
    if entry is None:
        return None

    env_var_name = named_value_env_var(identifier)
    env_override = os.environ.get(env_var_name)
    if env_override is not None:
        return ResolvedNamedValue(
            name=name,
            value=env_override,
            is_secret=entry.secret or entry.value_from_key_vault is not None,
            source="env",
            env_var_name=env_var_name,
        )

    if entry.value is not None:
        return ResolvedNamedValue(
            name=name,
            value=entry.value,
            is_secret=entry.secret,
            source="config",
            env_var_name=env_var_name,
        )

    return ResolvedNamedValue(
        name=name,
        value=_local_vault_value(
            entry.value_from_key_vault.secret_id, config, entry.value_from_key_vault.identity_client_id
        )
        if entry.value_from_key_vault
        else None,
        is_secret=True,
        source="key_vault",
        env_var_name=env_var_name,
    )


class NamedValueResolutionMiddleware:
    """Use one secret snapshot for execution, projection, and redaction."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        with named_value_resolution_scope():
            await self.app(scope, receive, send)


def resolve_named_value(config: GatewayConfig, name: str) -> ResolvedNamedValue | None:
    identifier, _ = _find_named_value(config, name)
    snapshot = _request_resolutions.get()
    key = (id(config), identifier)
    if snapshot is None:
        return _resolve_named_value(config, name)
    if key not in snapshot:
        snapshot[key] = (config, _resolve_named_value(config, name))
    return snapshot[key][1]


def named_values_are_dynamic(config: GatewayConfig) -> bool:
    return any(
        entry.value_from_key_vault is not None or named_value_env_var(identifier) in os.environ
        for identifier, entry in config.named_values.items()
    )


def resolve_named_values_in_text(text: str, config: GatewayConfig) -> str:
    def _replace(match: re.Match[str]) -> str:
        resolved = resolve_named_value(config, match.group(1).strip())
        if resolved is None or resolved.value is None:
            return match.group(0)
        return resolved.value

    return NAMED_VALUE_PATTERN.sub(_replace, text)


def validate_named_value_references(text: str, config: GatewayConfig) -> None:
    """Reject policy references that do not name a configured value."""
    missing = sorted(
        {
            match.group(1).strip()
            for match in NAMED_VALUE_PATTERN.finditer(text)
            if _find_named_value(config, match.group(1).strip())[1] is None
        }
    )
    if missing:
        # Microsoft documents the reference syntax and deletion constraint, but
        # not the management API's exact missing-reference error text.
        names = ", ".join(missing)
        raise ValueError(f"unknown named value referenced by policy: {names}")


def secret_named_value_map(config: GatewayConfig) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in config.named_values:
        resolved = resolve_named_value(config, name)
        if resolved is None or not resolved.is_secret or not resolved.value:
            continue
        out[name] = resolved.value
    return out


def mask_secret_text(value: str, config: GatewayConfig) -> str:
    out = value
    for secret in secret_named_value_map(config).values():
        out = out.replace(secret, "***")
    return out


def mask_secret_data(value: Any, config: GatewayConfig) -> Any:
    secrets = secret_named_value_map(config)

    def redact(text: str) -> str:
        for secret in secrets.values():
            text = text.replace(secret, "***")
        return text

    def walk(item):
        if isinstance(item, str):
            return redact(item)
        if isinstance(item, bytes):
            return redact(item.decode("utf-8", errors="replace"))
        if isinstance(item, dict):
            return {str(key): walk(child) for key, child in item.items()}
        if isinstance(item, list):
            return [walk(child) for child in item]
        return item

    return walk(value)
