#!/usr/bin/env python3
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from app.config import GatewayConfig, RouteConfig
from app.main import create_app

FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "apim_samples"


@dataclass(frozen=True)
class FixtureEntry:
    id: str
    source: str
    status: str
    notes: str


def _load_manifest() -> list[FixtureEntry]:
    manifest_path = FIXTURE_ROOT / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    return [FixtureEntry(**entry) for entry in data]


def _load_fixture_file(fixture_id: str, name: str) -> Any:
    path = FIXTURE_ROOT / fixture_id / name
    if name.endswith(".json"):
        return json.loads(path.read_text(encoding="utf-8"))
    return path.read_text(encoding="utf-8")


def _decode_request_body(content: bytes) -> str:
    if not content:
        return ""
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("utf-8", errors="replace")


def _assert_subset(expected: Any, actual: Any, *, path: str = "root") -> None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            raise AssertionError(f"{path}: expected dict, got {type(actual).__name__}")
        for key, expected_value in expected.items():
            if key not in actual:
                raise AssertionError(f"{path}: missing key {key!r}")
            _assert_subset(expected_value, actual[key], path=f"{path}.{key}")
        return

    if isinstance(expected, list):
        if not isinstance(actual, list):
            raise AssertionError(f"{path}: expected list, got {type(actual).__name__}")
        if len(expected) != len(actual):
            raise AssertionError(f"{path}: expected list length {len(expected)}, got {len(actual)}")
        for index, expected_value in enumerate(expected):
            _assert_subset(expected_value, actual[index], path=f"{path}[{index}]")
        return

    if expected != actual:
        raise AssertionError(f"{path}: expected {expected!r}, got {actual!r}")


def _run_fixture(entry: FixtureEntry) -> None:
    policy_xml = _load_fixture_file(entry.id, "policy.xml")
    request_spec = _load_fixture_file(entry.id, "request.json")
    expected = _load_fixture_file(entry.id, "expected.json")

    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["upstream"] = {
            "method": req.method,
            "path": req.url.path,
            "query": dict(req.url.params),
            "headers": {key.lower(): value for key, value in req.headers.items()},
            "body": _decode_request_body(req.content),
        }
        return httpx.Response(200, json={"ok": True})

    config_overrides = request_spec.get("config", {})
    path_prefix = config_overrides.get("path_prefix", "/sample")
    upstream_path_prefix = config_overrides.get("upstream_path_prefix", "")

    app = create_app(
        config=GatewayConfig(
            allow_anonymous=config_overrides.get("allow_anonymous", True),
            policy_fragments=config_overrides.get("policy_fragments", {}),
            routes=[
                RouteConfig(
                    name="fixture",
                    path_prefix=path_prefix,
                    upstream_base_url="http://upstream",
                    upstream_path_prefix=upstream_path_prefix,
                    policies_xml=policy_xml,
                )
            ],
        ),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with TestClient(app) as client:
        response = client.request(
            request_spec.get("method", "GET"),
            request_spec["path"],
            params=request_spec.get("query", {}),
            headers=request_spec.get("headers", {}),
            content=(request_spec.get("body") or "").encode("utf-8"),
        )

    actual: dict[str, Any] = {
        "status_code": response.status_code,
        "headers": {key.lower(): value for key, value in response.headers.items()},
        "body_text": response.text,
        "upstream": captured.get("upstream"),
    }
    if response.headers.get("content-type", "").startswith("application/json"):
        actual["json"] = response.json()

    _assert_subset(expected, actual, path=entry.id)


def run_checks() -> dict[str, Any]:
    supported: list[str] = []
    adapted: list[str] = []
    unsupported: list[FixtureEntry] = []
    failures: list[str] = []

    for entry in _load_manifest():
        if entry.status == "unsupported":
            unsupported.append(entry)
            continue

        try:
            _run_fixture(entry)
        except Exception as exc:
            failures.append(f"{entry.id}: {exc}")
            continue

        if entry.status == "supported":
            supported.append(entry.id)
        elif entry.status == "adapted":
            adapted.append(entry.id)
        else:
            failures.append(f"{entry.id}: unknown status {entry.status}")

    return {
        "supported": supported,
        "adapted": adapted,
        "unsupported": unsupported,
        "failures": failures,
    }


def main() -> int:
    result = run_checks()

    if result["supported"]:
        print(f"Supported fixtures passed: {', '.join(result['supported'])}")
    if result["adapted"]:
        print(f"Adapted fixtures passed: {', '.join(result['adapted'])}")
    if result["unsupported"]:
        print("Unsupported fixtures:")
        for entry in result["unsupported"]:
            print(f"- {entry.id}: {entry.notes}")
    if result["failures"]:
        print("Compatibility failures:")
        for failure in result["failures"]:
            print(f"- {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
