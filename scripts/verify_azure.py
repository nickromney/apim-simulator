#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx


def _load_cases(path: str) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Verification cases must be a JSON array")
    if not data or not all(isinstance(item, dict) for item in data):
        raise ValueError("Verification requires a nonempty array of case objects")
    for item in data:
        path = item.get("path", "/")
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
            raise ValueError("Case paths must be local absolute paths")
    return data


def _response_body(response: httpx.Response) -> Any:
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        try:
            return response.json()
        except json.JSONDecodeError:
            return response.text
    return response.text


def _selected_headers(response: httpx.Response, names: list[str]) -> dict[str, str]:
    headers = {key.lower(): value for key, value in response.headers.items()}
    return {name.lower(): headers.get(name.lower(), "") for name in names}


def _compare_case(
    client: httpx.Client,
    case: dict,
    *,
    simulator_base_url: str,
    azure_base_url: str,
    simulator_headers: dict | None = None,
    azure_headers: dict | None = None,
) -> str | None:
    """Replay one case against both gateways. Returns a failure line, or None.

    Comparison stops at the first difference, because a status mismatch makes
    the header and body comparisons meaningless rather than additionally
    informative.
    """
    method = str(case.get("method") or "GET").upper()
    path = str(case.get("path") or "/")
    query = case.get("query") or {}
    headers = case.get("headers") or {}
    body = case.get("body_text")
    compare_headers = list(case.get("compare_headers") or [])

    simulator = client.request(
        method,
        f"{simulator_base_url}{path}",
        params=query,
        headers={**(simulator_headers or {}), **headers},
        content=body,
    )
    azure = client.request(
        method, f"{azure_base_url}{path}", params=query, headers={**(azure_headers or {}), **headers}, content=body
    )

    expected = case.get("expected_status")
    if expected is not None and (simulator.status_code != expected or azure.status_code != expected):
        return f"{path}: expected {expected}, simulator {simulator.status_code}, Azure {azure.status_code}"
    if "expected_body" in case:
        if _response_body(simulator) != case["expected_body"] or _response_body(azure) != case["expected_body"]:
            return f"{path}: response does not match expected body"
    if simulator.status_code != azure.status_code:
        return f"{path}: status {simulator.status_code} != {azure.status_code}"

    if compare_headers:
        simulator_headers = _selected_headers(simulator, compare_headers)
        azure_headers = _selected_headers(azure, compare_headers)
        if simulator_headers != azure_headers:
            return f"{path}: header mismatch {simulator_headers} != {azure_headers}"

    if case.get("compare_body", True) and _response_body(simulator) != _response_body(azure):
        return f"{path}: response body mismatch"
    return None


def _run_cases(client, cases, simulator_base_url, azure_base_url, simulator_headers, azure_headers):
    failures = []
    results = []
    for case in cases:
        try:
            failure = _compare_case(
                client,
                case,
                simulator_base_url=simulator_base_url,
                azure_base_url=azure_base_url,
                simulator_headers=simulator_headers,
                azure_headers=azure_headers,
            )
        except httpx.HTTPError as error:
            failure = f"{case.get('path', '/')}: transport failure ({type(error).__name__})"
        results.append({"name": case.get("name", case.get("path", "/")), "passed": failure is None})
        if failure is not None:
            failures.append(failure)
    return failures, results


def main() -> int:
    """Replay recorded cases against the simulator and a live Azure APIM."""
    cases_path = os.environ.get("VERIFY_CASES", "").strip()
    if not cases_path:
        print("VERIFY_CASES must point to a JSON file describing replay cases.", file=sys.stderr)
        return 2

    simulator_base_url = os.environ.get("SIMULATOR_BASE_URL", "http://localhost:8000").rstrip("/")
    azure_base_url = os.environ.get("AZURE_APIM_BASE_URL", "").rstrip("/")
    if not azure_base_url:
        print("AZURE_APIM_BASE_URL must be set for live verification.", file=sys.stderr)
        return 2

    simulator_headers = json.loads(os.environ.get("SIMULATOR_HEADERS", "{}"))
    azure_headers = json.loads(os.environ.get("AZURE_APIM_HEADERS", "{}"))
    with httpx.Client(timeout=60.0) as client:
        cases = _load_cases(cases_path)
        failures, results = _run_cases(
            client, cases, simulator_base_url, azure_base_url, simulator_headers, azure_headers
        )

    if report := os.environ.get("VERIFY_REPORT", ""):
        Path(report).write_text(
            json.dumps(
                {"cases": len(cases), "passed": len(cases) - len(failures), "failures": failures, "results": results},
                indent=2,
            )
            + "\n"
        )
    if failures:
        print("Verification failures:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("Azure verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
