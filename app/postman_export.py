"""Deterministic Postman v2.1 collections for the public gateway contract."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any
from urllib.parse import parse_qsl, quote

from app.config import ApiConfig, ApiVersionSetConfig, GatewayConfig
from app.openapi_export import build_openapi

SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"


def redact_collection(collection: dict[str, Any], config: GatewayConfig) -> dict[str, Any]:
    """Scrub configured credentials even when copied into public example metadata."""
    from app.named_values import secret_named_value_map

    secrets = {config.admin_token, config.tenant_access.primary_key, config.tenant_access.secondary_key}
    secrets.update(config.subscription.keys)
    for subscription in config.subscription.subscriptions.values():
        secrets.update((subscription.keys.primary, subscription.keys.secondary))
    for backend in config.backends.values():
        secrets.update((backend.basic_password, backend.authorization_parameter))
        secrets.update(backend.header_credentials.values())
        secrets.update(backend.query_credentials.values())
    secrets.update(secret_named_value_map(config).values())
    patterns = [
        re.compile((r"(?<!\w)" + re.escape(value) + r"(?!\w)") if len(value) < 8 else re.escape(value))
        for value in sorted((value for value in secrets if value), key=len, reverse=True)
    ]
    result = deepcopy(collection)
    _redact_fields(result["info"], ("name",), patterns)
    for item in result["item"]:
        _redact_fields(item, ("name",), patterns)
        request = item["request"]
        _redact_fields(request, ("description",), patterns)
        for header in request["header"]:
            if header["key"].casefold() != "content-type":
                _redact_fields(header, ("value",), patterns)
        url = request["url"]
        for entry in url.get("query", []) + url.get("variable", []):
            _redact_fields(entry, ("value",), patterns)
        suffix = "&".join(
            quote(entry["key"], safe="") + "=" + quote(entry["value"], safe="{}")
            for entry in url.get("query", [])
            if not entry.get("disabled")
        )
        url["raw"] = url["raw"].partition("?")[0] + ("?" + suffix if suffix else "")
    return result


def _redact_fields(payload: dict[str, Any], fields: tuple[str, ...], patterns: list[re.Pattern]) -> None:
    for field in fields:
        value = payload.get(field)
        if not isinstance(value, str) or value == "Bearer {{bearer_token}}" or re.fullmatch(r"\{\{[^{}]+\}\}", value):
            continue
        for pattern in patterns:
            value = pattern.sub("[redacted]", value)
        payload[field] = value


def _value(parameter: dict[str, Any]) -> str:
    # Defaults are part of the public contract; example values are never exported.
    name = re.sub(r"[^a-z0-9]", "", parameter["name"].casefold())
    if any(part in name for part in ("password", "secret", "token", "apikey", "subscriptionkey", "authorization")):
        return ""
    value = parameter.get("schema", {}).get("default", "")
    return value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))


def _body(content: dict[str, Any]) -> tuple[dict[str, Any], str]:
    content_type, media = next(iter(content.items()))
    schema = media.get("schema", {})
    if content_type in {"application/x-www-form-urlencoded", "multipart/form-data"}:
        mode = "urlencoded" if content_type == "application/x-www-form-urlencoded" else "formdata"
        return {
            "mode": mode,
            mode: [{"key": name, "value": "", "type": "text"} for name in schema.get("properties", {})],
        }, content_type
    # Imported examples may embed unclassified secrets; users fill the body locally.
    body: dict[str, Any] = {"mode": "raw", "raw": "{}" if "json" in content_type else ""}
    if "json" in content_type:
        body["options"] = {"raw": {"language": "json"}}
    return body, content_type


def _query(template: str, parameters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    query = [
        {"key": key, "value": _value({"name": key, "schema": {"default": re.sub(r"\{([^{}]+)\}", r"{{\1}}", value)}})}
        for key, value in parse_qsl(template.partition("?")[2], keep_blank_values=True)
    ]
    for item in parameters:
        if item["in"] == "query":
            existing = next((entry for entry in query if entry["key"] == item["name"]), None)
            query = [entry for entry in query if entry["key"] != item["name"]]
            value = _value(item)
            if existing is not None and "default" not in item.get("schema", {}):
                value = existing["value"]
            query.append({"key": item["name"], "value": value, "disabled": existing is None and not item["required"]})
    return query


def _headers(parameters: list[dict[str, Any]], subscription_header: str) -> list[dict[str, str]]:
    headers = [{"key": item["name"], "value": _value(item)} for item in parameters if item["in"] == "header"]
    for header in headers:
        if header["key"].casefold() == "authorization":
            header["value"] = "Bearer {{bearer_token}}"
        elif header["key"].casefold() == subscription_header.casefold():
            header["value"] = "{{subscription_key}}"
    return headers


def build_postman(
    api: ApiConfig,
    *,
    version_set: ApiVersionSetConfig | None = None,
    subscription_header: str = "Ocp-Apim-Subscription-Key",
) -> dict[str, Any]:
    """Never export an upstream address, credential or executable script."""
    items = []
    for operation_id, operation in api.operations.items():
        # Export each operation separately so query-discriminated routes remain distinct.
        single = api.model_copy(update={"operations": {operation_id: operation}})
        document = build_openapi(single, version_set=version_set)
        path, methods = next(iter(document["paths"].items()))
        metadata = next(iter(methods.values()))
        base = document["servers"][0]["url"].rstrip("/")
        parameters = metadata["parameters"]
        path_values = [{"key": item["name"], "value": _value(item)} for item in parameters if item["in"] == "path"]
        public_path = re.sub(r"\{([^{}]+)\}", r":\1", base + path)
        query = _query(operation.url_template, parameters)
        headers = _headers(parameters, subscription_header)
        suffix = "&".join(
            quote(entry["key"], safe="") + "=" + quote(entry["value"], safe="{}")
            for entry in query
            if not entry.get("disabled")
        )
        url: dict[str, Any] = {
            "raw": "{{base_url}}" + public_path + ("?" + suffix if suffix else ""),
            "host": ["{{base_url}}"],
            "path": public_path.lstrip("/").split("/"),
        }
        if query:
            url["query"] = query
        if path_values:
            url["variable"] = path_values
        request: dict[str, Any] = {"method": operation.method, "header": headers, "url": url}
        if operation.description:
            request["description"] = operation.description
        content = metadata.get("requestBody", {}).get("content", {})
        if content:
            body, content_type = _body(content)
            request["body"] = body
            # Postman computes multipart boundaries and content length itself.
            if content_type != "multipart/form-data" and not any(
                h["key"].casefold() == "content-type" for h in headers
            ):
                headers.append({"key": "Content-Type", "value": content_type})
        request["header"] = [header for header in headers if header["key"].casefold() != "content-length"]
        items.append({"name": operation.name or operation_id, "request": request, "response": []})
    return {
        "info": {"name": api.name or api.path or "API", "schema": SCHEMA},
        "variable": [
            {"key": "base_url", "value": "http://localhost:8000", "type": "string"},
            {"key": "subscription_key", "value": "", "type": "string"},
            {"key": "bearer_token", "value": "", "type": "string"},
        ],
        "auth": {
            "type": "apikey",
            "apikey": [
                {"key": "key", "value": subscription_header, "type": "string"},
                {"key": "value", "value": "{{subscription_key}}", "type": "string"},
                {"key": "in", "value": "header", "type": "string"},
            ],
        },
        "item": items,
    }
