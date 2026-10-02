"""Compare the local architecture-patterns fixture with an existing APIM service.

Validation is offline and read-only. Azure writes require the explicit
``--execute`` flag and are confined to UUID-named APIs that this run owns.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import re
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / "examples" / "architecture-patterns" / "apim.json"
API_VERSION = "2022-08-01"
PATTERNS = {
    "routing": ("routing", "catalog"),
    "offloading": ("offloading", "catalog"),
    "gatekeeper": ("gatekeeper", "catalog"),
    "aggregation": ("aggregation", "overview"),
}
MOCKS = {
    "catalog": (
        200,
        {
            "id": "tea",
            "name": "Tea",
            "price": "8.50",
            "internal": "private",
            "backend": "private-services",
            "gatewayTransform": "",
        },
    ),
    "orders": (200, {"open": 3, "backend": "private-services"}),
    "order-service": (200, {"open": 3, "backend": "order-service"}),
    "profile": (200, {"tier": "gold", "backend": "private-services"}),
    "orders-fail": (503, {"detail": "demo upstream failure"}),
}


class AzureCliError(RuntimeError):
    """A sanitized Azure CLI failure, without command output or credentials."""

    def __init__(self, code: str = "AzureCliFailure", detail: str = "") -> None:
        self.code = code
        super().__init__(f"APIM management request failed ({code}). {detail}".strip())


@dataclass(frozen=True)
class Target:
    subscription: str
    resource_group: str
    service: str
    gateway_url: str

    @property
    def gateway_host(self) -> str:
        return urlparse(self.gateway_url).netloc

    def resource_url(self, api_id: str) -> str:
        return (
            "https://management.azure.com/subscriptions/"
            f"{self.subscription}/resourceGroups/{self.resource_group}"
            "/providers/Microsoft.ApiManagement/service/"
            f"{self.service}/apis/{api_id}?api-version={API_VERSION}"
        )

    def operation_url(self, api_id: str, operation_id: str) -> str:
        base = self.resource_url(api_id).split("?", maxsplit=1)[0]
        return f"{base}/operations/{operation_id}?api-version={API_VERSION}"

    def policy_url(self, api_id: str, operation_id: str | None = None) -> str:
        base = self.resource_url(api_id).split("?", maxsplit=1)[0]
        if operation_id:
            base += f"/operations/{operation_id}"
        return f"{base}/policies/policy?api-version={API_VERSION}"


def management_request(
    method: str, url: str, body: dict[str, Any] | None = None, *, missing_ok: bool = False
) -> dict[str, Any] | None:
    command = ["az", "rest", "--method", method, "--url", url, "--only-show-errors", "--output", "json"]
    if body is not None:
        command.extend(["--headers", "Content-Type=application/json", "--body", json.dumps(body)])
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=90)
    except subprocess.TimeoutExpired as exc:
        raise AzureCliError("AzureCliTimeout") from exc
    if result.returncode:
        code = _azure_error_code(result.stderr)
        if missing_ok and code in {"ResourceNotFound", "NotFound"}:
            return None
        detail = _validation_detail(result.stderr) if code == "ValidationError" else ""
        raise AzureCliError(code, detail)
    # APIM policy writes can return XML even when the submitted document is JSON.
    # Only GET metadata is used by the ownership checks.
    if method.lower() != "get" or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AzureCliError("InvalidManagementResponse") from exc


def _azure_error_code(stderr: str) -> str:
    try:
        payload = json.loads(stderr)
        error = payload.get("error", payload)
        code = error.get("code") if isinstance(error, dict) else None
        return str(code or "AzureCliFailure")
    except json.JSONDecodeError:
        for known in ("ResourceNotFound", "NotFound", "ValidationError", "InvalidRequestContent"):
            if known in stderr:
                return known
        return "AzureCliFailure"


def _validation_detail(stderr: str) -> str:
    try:
        payload, _ = json.JSONDecoder().raw_decode(stderr[stderr.index("{") :])
        error = payload.get("error", payload)
        details = error.get("details") or [{"message": error.get("message", "Policy validation failed.")}]
        return " ".join(str(item.get("message", "")) for item in details)[:1000]
    except (ValueError, TypeError, AttributeError):
        return "Policy validation failed."


def operation_resource(operation: dict[str, Any], status: int = 200) -> dict[str, Any]:
    return {
        "properties": {
            "displayName": operation["name"],
            "method": operation["method"],
            "urlTemplate": operation["url_template"],
            "responses": [
                {
                    "statusCode": status,
                    "description": "fixture",
                    "representations": [{"contentType": "application/json"}],
                }
            ],
        }
    }


def fixture_api_resource(api_id: str, api_path: str, owner: str) -> dict[str, Any]:
    return {
        "properties": {
            "displayName": f"APIM simulator fixture {api_id[-8:]}",
            "description": owner,
            "path": api_path,
            "protocols": ["https"],
            "serviceUrl": "https://example.invalid",
            "subscriptionRequired": False,
        }
    }


def pattern_api_resource(source: dict[str, Any], api_path: str, service_url: str, owner: str) -> dict[str, Any]:
    return {
        "properties": {
            "displayName": f"APIM simulator comparison {source['name']}",
            "description": owner,
            "path": api_path,
            "protocols": ["https"],
            "serviceUrl": service_url,
            "subscriptionRequired": False,
        }
    }


def raw_policy(xml: str) -> dict[str, Any]:
    return {"properties": {"format": "rawxml", "value": xml}}


def fixture_policy(status: int, payload: dict[str, Any]) -> str:
    body = json.dumps(payload, separators=(",", ":"))
    if "gatewayTransform" in payload:
        static = {key: value for key, value in payload.items() if key != "gatewayTransform"}
        prefix = json.dumps(static, separators=(",", ":"))[:-1] + ',"gatewayTransform":"'
        expr = f'@({json.dumps(prefix)} + context.Request.Headers.GetValueOrDefault("x-gateway-transform", "") + {json.dumps('"}')})'
        body = html.escape(expr)
    return (
        '<policies><inbound><return-response><set-status code="'
        f'{status}" reason="fixture" /><set-header name="Content-Type" '
        f'exists-action="override"><value>application/json</value></set-header>'
        '<set-header name="X-Fixture-Request-Id" exists-action="override"><value>@(context.RequestId.ToString())</value></set-header><set-body>'
        f"{body}</set-body></return-response></inbound><backend /><outbound /><on-error /></policies>"
    )


def inject_loopback(xml: str, host: str, fixture_path: str) -> str:
    replacements = {
        "http://pattern-backends:1/api/orders": f"https://127.0.0.1:1/{fixture_path}/api/orders",
        "http://pattern-backends:8000/api/orders-fail": f"https://127.0.0.1/{fixture_path}/api/orders-fail",
        "http://pattern-backends:8000/api/orders": f"https://127.0.0.1/{fixture_path}/api/orders",
        "http://pattern-backends:8000/api/profile": f"https://127.0.0.1/{fixture_path}/api/profile",
    }
    for original, replacement in replacements.items():
        xml = xml.replace(original, replacement)
    xml = re.sub(
        r"(<send-request\b[^>]*>)(.*?)(</send-request>)",
        lambda match: (
            match.group(1)
            + match.group(2).replace(
                "</set-method>",
                f'</set-method><set-header name="Host" exists-action="override"><value>{host}</value></set-header>',
            )
            + match.group(3)
        ),
        xml,
        flags=re.DOTALL,
    )
    return xml


def policy_with_backend_host(xml: str, host: str) -> str:
    header = f'<set-header name="Host" exists-action="override"><value>{host}</value></set-header>'
    if "<inbound>" not in xml:
        raise ValueError("APIM policy has no inbound section")
    return xml.replace("<inbound>", f"<inbound>{header}", 1)


def validate_fixture(data: dict[str, Any]) -> list[str]:
    apis = data.get("apis")
    if not isinstance(apis, dict):
        raise ValueError("architecture-patterns fixture must contain an APIs object")
    missing = sorted({item[0] for item in PATTERNS.values()} - apis.keys())
    if missing:
        raise ValueError(f"architecture-patterns fixture is missing APIs: {', '.join(missing)}")
    checked = []
    for api_id, (source, operation_id) in PATTERNS.items():
        api = apis[source]
        operation = api.get("operations", {}).get(operation_id)
        xml = api.get("policies_xml", "")
        if not operation or not xml:
            raise ValueError(f"APIM fixture {source} must define operation {operation_id} and policy XML")
        ET.fromstring(xml)
        checked.append(api_id)
    return checked


def validate_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    apis = data.get("apis")
    if not isinstance(apis, dict):
        raise ValueError(f"{path} must contain an APIs object")
    count = 0
    for api in apis.values():
        xml = api.get("policies_xml")
        if xml:
            ET.fromstring(xml)
            count += 1
    try:
        label = str(path.resolve().relative_to(ROOT))
    except ValueError:
        label = path.name
    return {"file": label, "apis": len(apis), "xml_well_formed": count}


def build_token(xml: str) -> str:
    root = ET.fromstring(xml)
    key = root.findtext("./inbound/validate-jwt/issuer-signing-keys/key")
    issuer = root.findtext("./inbound/validate-jwt/issuers/issuer")
    audience = root.findtext("./inbound/validate-jwt/audiences/audience")
    if not (key and issuer and audience):
        raise ValueError("Gatekeeper fixture must declare one HS256 key, issuer, and audience")
    claims = {
        "sub": f"azure-pattern-probe-{uuid.uuid4().hex}",
        "iss": issuer,
        "aud": audience,
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
    }
    return jwt.encode(claims, base64.b64decode(key), algorithm="HS256")


def compare_response(
    response: httpx.Response,
    status: int,
    body: Any | None = None,
    headers: dict[str, str] | None = None,
    body_text: str | None = None,
) -> dict[str, Any]:
    passed = response.status_code == status
    if body is not None:
        try:
            passed = passed and response.json() == body
        except ValueError:
            passed = False
    if body_text is not None:
        passed = passed and response.text == body_text
    observed_headers = {}
    for name, value in (headers or {}).items():
        actual = response.headers.get(name)
        observed_headers[name] = actual
        passed = passed and actual == value
    return {
        "passed": passed,
        "expected_status": status,
        "actual_status": response.status_code,
        "headers": observed_headers,
    }


def probe_cases(
    client: httpx.Client, root: str, apis: dict[str, str], fixture: dict[str, Any], azure_cache_evidence: bool = False
) -> list[dict[str, Any]]:
    cases = []

    def url(key: str) -> str:
        return f"{root.rstrip('/')}/{apis[key].lstrip('/')}"

    routes = (("routing:catalog", MOCKS["catalog"][1]), ("routing:orders", MOCKS["order-service"][1]))
    for key, expected in routes:
        response = client.get(url(key))
        cases.append({"name": key, **compare_response(response, 200, expected)})
    offload_url = url("offloading:catalog") + f"?run={uuid.uuid4().hex}"
    offload_headers = {"x-client-id": f"apim-compare-{uuid.uuid4().hex}"}
    offload_body = {**MOCKS["catalog"][1], "gatewayTransform": "applied"}
    before = catalog_count(client, root) if not azure_cache_evidence else None
    first = client.get(offload_url, headers=offload_headers)
    after_first = catalog_count(client, root) if not azure_cache_evidence else None
    second = client.get(offload_url, headers=offload_headers)
    after_second = catalog_count(client, root) if not azure_cache_evidence else None
    cases.append({"name": "offloading-first", **compare_response(first, 200, offload_body)})
    cached = compare_response(second, 200, offload_body, {"x-pattern": "offloading"})
    if azure_cache_evidence:
        first_id = first.headers.get("x-fixture-request-id")
        second_id = second.headers.get("x-fixture-request-id")
        cached["fixture_request_id_reused"] = bool(first_id and first_id == second_id)
        cached["passed"] = cached["passed"] and cached["fixture_request_id_reused"]
    else:
        cached["backend_call_count_unchanged"] = after_first == after_second and after_first == before + 1
        cached["passed"] = cached["passed"] and cached["backend_call_count_unchanged"]
    cases.append({"name": "offloading-cache", **cached})
    denied = client.get(url("gatekeeper:catalog"))
    cases.append({"name": "gatekeeper-missing-jwt", **compare_response(denied, 401)})
    token = build_token(fixture["apis"]["gatekeeper"]["policies_xml"])
    headers = {"Authorization": f"Bearer {token}"}
    allowed = client.get(url("gatekeeper:catalog"), headers=headers)
    cases.append({"name": "gatekeeper-valid-jwt", **compare_response(allowed, 200, MOCKS["catalog"][1])})
    second_allowed = client.get(url("gatekeeper:catalog"), headers=headers)
    cases.append({"name": "gatekeeper-second-valid-jwt", **compare_response(second_allowed, 200, MOCKS["catalog"][1])})
    throttled = client.get(url("gatekeeper:catalog"), headers=headers)
    cases.append({"name": "gatekeeper-rate-limit", **compare_response(throttled, 429)})
    aggregation = client.get(url("aggregation:overview"))
    aggregate_body = {
        "orders": {"open": 3, "backend": "private-services"},
        "profile": {"tier": "gold", "backend": "private-services"},
    }
    cases.append(
        {
            "name": "aggregation",
            **compare_response(aggregation, 200, aggregate_body, {"x-aggregation": "orders,profile"}),
        }
    )
    partial = client.get(url("aggregation:overview") + "?fail=true")
    cases.append(
        {
            "name": "aggregation-backend-error",
            **compare_response(partial, 502, body_text="aggregation source returned an error"),
        }
    )
    transport = client.get(url("aggregation:overview") + "?fail=transport")
    cases.append(
        {
            "name": "aggregation-transport-error",
            **compare_response(transport, 502, body_text="aggregation callout failed"),
        }
    )
    return cases


def catalog_count(client: httpx.Client, root: str) -> int:
    response = client.get(f"{root.rstrip('/')}/diagnostics/stats")
    response.raise_for_status()
    return int(response.json().get("catalog", -1))


def fixture_operations(target: Target, api_id: str) -> None:
    for name, (status, body) in MOCKS.items():
        operation_id = f"mock-{name}"
        mock_path = "/api/order-service/orders" if name == "order-service" else f"/api/{name}"
        operation = {"name": f"mock {name}", "method": "GET", "url_template": mock_path}
        management_request("put", target.operation_url(api_id, operation_id), operation_resource(operation, status))
        management_request("put", target.policy_url(api_id, operation_id), raw_policy(fixture_policy(status, body)))


def deploy_api(target: Target, api_id: str, resource: dict[str, Any], owner: str, owned: dict[str, str]) -> None:
    if management_request("get", target.resource_url(api_id), missing_ok=True):
        raise AzureCliError("ResourceAlreadyExists")
    owned[api_id] = owner
    management_request("put", target.resource_url(api_id), resource)


def delete_owned_api(target: Target, api_id: str, owner: str) -> bool:
    try:
        current = management_request("get", target.resource_url(api_id), missing_ok=True)
    except AzureCliError:
        return False
    if not current:
        return True
    if current.get("properties", {}).get("description") != owner:
        return False
    management_request("delete", target.resource_url(api_id))
    return True


def deploy_pattern_apis(
    target: Target,
    fixture: dict[str, Any],
    run_id: str,
    fixture_path: str,
    owner_prefix: str,
    owned: dict[str, str],
) -> tuple[dict[str, str], list[str]]:
    endpoints: dict[str, str] = {}
    accepted = []
    for pattern_key, (source_key, _) in PATTERNS.items():
        source = fixture["apis"][source_key]
        api_id = f"aps-{pattern_key}-{run_id}"
        owner = owner_prefix + pattern_key
        api_path = f"aps-{run_id}/{pattern_key}"
        resource = pattern_api_resource(source, api_path, f"https://127.0.0.1/{fixture_path}/api", owner)
        deploy_api(target, api_id, resource, owner, owned)
        operation_endpoints, operation_policies = deploy_pattern_operations(
            target, source, api_id, api_path, fixture_path, pattern_key
        )
        endpoints.update(operation_endpoints)
        accepted.extend(operation_policies)
        policy = policy_with_backend_host(
            inject_loopback(source["policies_xml"], target.gateway_host, fixture_path), target.gateway_host
        )
        management_request("put", target.policy_url(api_id), raw_policy(policy))
        accepted.append(pattern_key)
    return endpoints, accepted


def deploy_pattern_operations(
    target: Target, source: dict[str, Any], api_id: str, api_path: str, fixture_path: str, pattern_key: str
) -> tuple[dict[str, str], list[str]]:
    endpoints = {}
    accepted = []
    for operation_id, operation in source["operations"].items():
        management_request("put", target.operation_url(api_id, operation_id), operation_resource(operation))
        if operation.get("upstream_base_url"):
            policy = operation_backend_policy(target, fixture_path)
            management_request("put", target.policy_url(api_id, operation_id), raw_policy(policy))
            accepted.append(f"{pattern_key}:{operation_id}-backend")
        endpoints[f"{pattern_key}:{operation_id}"] = f"{api_path}{operation['url_template']}"
    return endpoints, accepted


def operation_backend_policy(target: Target, fixture_path: str) -> str:
    url = f"https://127.0.0.1/{fixture_path}/api/order-service"
    host = target.gateway_host
    return (
        "<policies><inbound>"
        f'<set-backend-service base-url="{url}" />'
        f'<set-header name="Host" exists-action="override"><value>{host}</value></set-header>'
        "<base /></inbound><backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>"
    )


def cleanup_owned(target: Target, owned: dict[str, str]) -> list[dict[str, Any]]:
    outcomes = []
    for api_id, owner in reversed(list(owned.items())):
        try:
            deleted = delete_owned_api(target, api_id, owner)
        except (AzureCliError, OSError):
            deleted = False
        outcomes.append({"api": api_id, "deleted": deleted})
    return outcomes


def extra_policy_sources(paths: list[Path]):
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        for key, source in data["apis"].items():
            if source.get("policies_xml"):
                yield f"{path.parent.name}/{path.name}:{key}", source


def validate_extra_policies(target: Target, paths: list[Path], run_id: str, owned: dict[str, str]) -> list[str]:
    accepted = []
    for index, (label, source) in enumerate(extra_policy_sources(paths)):
        api_id = f"aps-extra-{index}-{run_id}"
        owner = f"apim-simulator-azure-compare:{run_id}:extra-{index}"
        resource = pattern_api_resource(source, api_id, "https://example.invalid", owner)
        deploy_api(target, api_id, resource, owner, owned)
        management_request("put", target.policy_url(api_id), raw_policy(source["policies_xml"]))
        accepted.append(label)
    return accepted


def execute_comparison(
    target: Target,
    fixture: dict[str, Any],
    timeout: float,
    validate_only: bool,
    configs: list[Path] | None = None,
    runtime_probe: Any = None,
) -> dict[str, Any]:
    run_id = uuid.uuid4().hex[:12]
    fixture_id = f"aps-fixture-{run_id}"
    fixture_path = fixture_id
    owner_prefix = f"apim-simulator-azure-compare:{run_id}:"
    owned: dict[str, str] = {}
    results: list[dict[str, Any]] = []
    accepted: list[str] = []
    try:
        deploy_api(
            target,
            fixture_id,
            fixture_api_resource(fixture_id, fixture_path, owner_prefix + "fixture"),
            owner_prefix + "fixture",
            owned,
        )
        fixture_operations(target, fixture_id)
        endpoints, accepted = deploy_pattern_apis(target, fixture, run_id, fixture_path, owner_prefix, owned)
        if validate_only:
            accepted.extend(validate_extra_policies(target, configs or [], run_id, owned))
            report = {
                "mode": "azure-policy-acceptance",
                "status": "passed",
                "target": "configured-apim-service",
                "policies_accepted": accepted,
            }
        else:
            if runtime_probe is not None:
                results = runtime_probe(target.gateway_url, endpoints, fixture, timeout)
            else:
                with httpx.Client(timeout=timeout, verify=True) as client:
                    results = probe_cases(client, target.gateway_url, endpoints, fixture, azure_cache_evidence=True)
            report = {
                "mode": "azure-runtime",
                "status": "passed" if all(case["passed"] for case in results) else "failed",
                "target": "configured-apim-service",
                "cases": results,
            }
    except (AzureCliError, OSError, httpx.HTTPError, ValueError) as exc:
        report = {
            "mode": "azure-policy-acceptance" if validate_only else "azure-runtime",
            "status": "incomplete",
            "target": "configured-apim-service",
            "error": type(exc).__name__,
            "error_code": getattr(exc, "code", None),
            "message": f"APIM management request failed ({exc.code})."
            if isinstance(exc, AzureCliError)
            else "The request could not be completed.",
            "cases": results,
        }
    finally:
        cleanup = cleanup_owned(target, owned)
    report["cleanup"] = [
        {**item, "api": re.sub(r"-[a-f0-9]{12}$", "-<run-id>", item["api"])} if "api" in item else item
        for item in cleanup
    ]
    if any(not item["deleted"] for item in report["cleanup"]):
        report["status"] = "cleanup-incomplete"
    return report


def run_live(
    target: Target,
    fixture: dict[str, Any],
    timeout: float,
    validate_only: bool = False,
    configs: list[Path] | None = None,
) -> dict[str, Any]:
    return execute_comparison(target, fixture, timeout, validate_only, configs)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subscription-id")
    parser.add_argument("--resource-group")
    parser.add_argument("--service-name")
    parser.add_argument("--gateway-url")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="with --execute, deploy and validate policies but skip gateway calls",
    )
    parser.add_argument(
        "--execute", action="store_true", help="create uniquely named temporary Azure APIs, compare, and clean them up"
    )
    parser.add_argument("--simulator-base-url", help="also run these cases against an already-running local simulator")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument(
        "--config",
        type=Path,
        action="append",
        help="additional config to parse and, with --execute --validate-only, validate on Azure (repeatable)",
    )
    parser.add_argument("--report", type=Path, help="write the sanitized JSON report to this path")
    return parser.parse_args(argv)


def validate_target(args: argparse.Namespace) -> Target:
    values = (args.subscription_id, args.resource_group, args.service_name)
    if not all(values):
        raise ValueError("--subscription-id, --resource-group, and --service-name are required for Azure execution")
    for value in values:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,90}", value):
            raise ValueError("Azure identifiers may contain only letters, digits, dot, underscore, and hyphen")
    gateway_url = args.gateway_url or f"https://{args.service_name}.azure-api.net"
    parsed = urlparse(gateway_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path not in ("", "/"):
        raise ValueError("--gateway-url must be an HTTPS gateway origin with no path")
    return Target(args.subscription_id, args.resource_group, args.service_name, gateway_url.rstrip("/"))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        checked = validate_fixture(fixture)
        configs = [
            FIXTURE_PATH,
            ROOT / "examples" / "architecture-patterns-extra" / "apim.json",
            ROOT / "examples" / "bff" / "apim.json",
            ROOT / "examples" / "bff" / "apim.internal.json",
        ]
        configs.extend(args.config or [])
        config_results = [validate_config(path) for path in dict.fromkeys(configs)]
        report: dict[str, Any] = {
            "mode": "validation-only",
            "status": "valid",
            "patterns": checked,
            "configs": config_results,
            "validation_scope": "JSON structure and XML well-formedness only; Azure policy/runtime parity is not claimed",
        }
        if args.execute:
            target = validate_target(args)
            report = run_live(target, fixture, args.timeout, args.validate_only, list(dict.fromkeys(configs[1:])))
        elif not args.validate_only:
            report["note"] = (
                "No Azure calls were made. Pass --execute with explicit target arguments to run comparison."
            )
        if args.simulator_base_url:
            local_apis = {
                f"{key}:{operation_id}": f"{fixture['apis'][source]['path']}{operation['url_template']}"
                for key, (source, _) in PATTERNS.items()
                for operation_id, operation in fixture["apis"][source]["operations"].items()
            }
            with httpx.Client(timeout=args.timeout) as client:
                local_results = probe_cases(client, args.simulator_base_url, local_apis, fixture)
            report["simulator"] = {
                "status": "passed" if all(item["passed"] for item in local_results) else "failed",
                "cases": local_results,
            }
            if report["simulator"]["status"] != "passed":
                report["status"] = "failed"
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        report = {"mode": "validation-only", "status": "invalid", "error": type(exc).__name__}
    output = json.dumps(report, sort_keys=True)
    if args.report:
        args.report.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if report["status"] in {"valid", "passed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
