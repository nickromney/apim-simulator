"""APIM diagnostic settings applied to local request telemetry, and the trace policy.

https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-app-insights
https://learn.microsoft.com/en-us/azure/api-management/trace-policy
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import ApiConfig, GatewayConfig, LoggerConfig, OperationConfig, TenantAccessConfig
from app.main import create_app
from app.policy import parse_policies_xml

TENANT = {"X-Apim-Tenant-Key": "diag-key"}

_POLICY = """\
<policies>
  <inbound>
    <base />
    <trace source="orders" severity="information"><message>@("looked up " + context.Request.Url.Path)</message>
      <metadata name="kind" value="lookup" /></trace>
    <trace source="orders" severity="verbose"><message>noisy detail</message></trace>
  </inbound>
  <backend><forward-request /></backend>
  <outbound><base /></outbound>
</policies>
"""


def _client(**diagnostic_overrides) -> TestClient:
    def handler(request: httpx.Request) -> httpx.Response:
        status = 500 if request.url.path.endswith("/fail") else 200
        return httpx.Response(status, json={"ok": status == 200}, headers={"x-backend": "b1", "x-secret": "s"})

    cfg = GatewayConfig(
        allow_anonymous=True,
        proxy_streaming=False,
        tenant_access=TenantAccessConfig(enabled=True, primary_key="diag-key"),
        loggers={"appinsights": LoggerConfig(logger_type="applicationInsights")},
        apis={
            "orders": ApiConfig(
                name="Orders",
                path="orders",
                upstream_base_url="http://backend",
                policies_xml=_POLICY,
                operations={
                    "get": OperationConfig(name="Get", method="GET", url_template="/{id}"),
                    "post": OperationConfig(name="Post", method="POST", url_template="/{id}"),
                },
            )
        },
    )
    app = create_app(config=cfg, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    client = TestClient(app)
    if diagnostic_overrides is not None:
        body = {"logger_id": "appinsights", **diagnostic_overrides}
        client.__enter__()
        resp = client.put("/apim/management/diagnostics/applicationinsights", json=body, headers=TENANT)
        assert resp.status_code == 200, resp.text
    return client


def _telemetry(client: TestClient, **params) -> list[dict]:
    resp = client.get("/apim/management/monitoring/telemetry", params=params, headers=TENANT)
    assert resp.status_code == 200
    return resp.json()["items"]


@pytest.mark.contract("OBS-DIAGNOSTIC-TELEMETRY")
def test_diagnostic_logs_selected_headers_bodies_and_masks_query() -> None:
    message = {"headers_to_log": ["x-backend", "content-type", "x-secret"], "body_bytes": 5}
    message["data_masking"] = {"headers": [{"mode": "Hide", "value": "x-secret"}], "query_params": []}
    client = _client(
        frontend_request={
            "headers_to_log": ["x-client"],
            "body_bytes": 4,
            "data_masking": {"headers": [], "query_params": [{"mode": "Mask", "value": "token"}]},
        },
        backend_response=message,
        frontend_response={"body_bytes": 100},
    )
    client.post("/orders/7?token=abc&keep=1", content=b"payload", headers={"x-client": "web"})
    (record,) = _telemetry(client)
    assert record["destination"] == "applicationinsights"
    assert record["url"].endswith("/orders/7?token=***&keep=1")
    assert record["frontend_request"] == {"headers": {"x-client": "web"}, "body": "payl", "body_truncated": True}
    assert record["backend_response"]["headers"] == {"x-backend": "b1", "content-type": "application/json"}
    assert record["backend_response"]["body"] == '{"ok"'
    assert record["frontend_response"]["body"] == '{"ok":true}'
    assert "client_ip" not in record
    client.__exit__(None, None, None)


@pytest.mark.contract("OBS-DIAGNOSTIC-TELEMETRY")
def test_sampling_drops_requests_but_always_log_errors_keeps_failures() -> None:
    client = _client(sampling_percentage=0, always_log_errors=True, log_client_ip=True)
    client.get("/orders/1")
    client.get("/orders/fail")
    records = _telemetry(client)
    assert [r["status_code"] for r in records] == [500]
    assert records[0]["success"] is False
    assert records[0]["client_ip"]
    client.__exit__(None, None, None)


def test_verbosity_filters_trace_policy_messages() -> None:
    client = _client(verbosity="information")
    client.get("/orders/1")
    (record,) = _telemetry(client)
    assert record["traces"] == [
        {
            "source": "orders",
            "severity": "information",
            "message": "looked up /orders/1",
            "metadata": {"kind": "lookup"},
        }
    ]
    client.__exit__(None, None, None)

    verbose = _client(verbosity="verbose")
    verbose.get("/orders/1")
    assert [t["message"] for t in _telemetry(verbose)[0]["traces"]] == ["looked up /orders/1", "noisy detail"]
    verbose.__exit__(None, None, None)


def test_w3c_correlation_uses_the_incoming_traceparent() -> None:
    client = _client(http_correlation_protocol="W3C")
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    client.get("/orders/1", headers={"traceparent": f"00-{trace_id}-00f067aa0ba902b7-01"})
    (record,) = _telemetry(client)
    assert record["correlation_protocol"] == "W3C"
    assert record["operation_id"] == trace_id
    client.__exit__(None, None, None)


@pytest.mark.contract("OBS-DIAGNOSTIC-TELEMETRY")
def test_api_scope_diagnostic_overrides_the_service_one() -> None:
    client = _client(sampling_percentage=100)
    resp = client.put(
        "/apim/management/apis/orders/diagnostics/applicationinsights",
        json={"logger_id": "appinsights", "frontend_request": {"headers_to_log": ["x-client"]}},
        headers=TENANT,
    )
    assert resp.status_code == 200, resp.text
    client.get("/orders/1", headers={"x-client": "mobile"})
    (record,) = _telemetry(client)
    assert record["diagnostic_scope"] == "api"
    assert record["frontend_request"] == {"headers": {"x-client": "mobile"}}
    listed = client.get("/apim/management/apis/orders/diagnostics", headers=TENANT).json()
    assert [item["id"] for item in listed] == ["orders:applicationinsights"]
    assert (
        client.delete("/apim/management/apis/orders/diagnostics/applicationinsights", headers=TENANT).status_code == 200
    )
    client.__exit__(None, None, None)


@pytest.mark.parametrize(
    ("path", "body", "message"),
    [
        ("/apim/management/diagnostics/other", {}, "diagnostic id must be one of"),
        ("/apim/management/diagnostics/applicationinsights", {"logger_id": "missing"}, "does not exist"),
        ("/apim/management/diagnostics/applicationinsights", {"sampling_percentage": 101}, "between 0 and 100"),
        ("/apim/management/diagnostics/applicationinsights", {"verbosity": "debug"}, "verbosity must be"),
        ("/apim/management/diagnostics/applicationinsights", {"frontend_request": {"body_bytes": 9000}}, "8192"),
    ],
)
def test_invalid_diagnostics_are_rejected(path: str, body: dict, message: str) -> None:
    client = _client(sampling_percentage=100)
    resp = client.put(path, json=body, headers=TENANT)
    assert resp.status_code == 400
    assert message in resp.json()["detail"]
    client.__exit__(None, None, None)


def test_no_diagnostic_means_no_telemetry_and_writes_need_the_tenant_key() -> None:
    client = _client(sampling_percentage=100)
    assert client.delete("/apim/management/diagnostics/applicationinsights", headers=TENANT).status_code == 200
    client.get("/orders/1")
    client.delete("/apim/management/monitoring/telemetry", headers=TENANT)
    client.get("/orders/1")
    assert _telemetry(client) == []
    assert client.put("/apim/management/diagnostics/applicationinsights", json={}).status_code in {401, 403}
    client.__exit__(None, None, None)


@pytest.mark.parametrize(
    ("policy", "message"),
    [
        ("<trace><message>m</message></trace>", "trace requires source"),
        ('<trace source="s" severity="debug"><message>m</message></trace>', "severity must be"),
        ('<trace source="s" />', "trace requires a message"),
        ('<trace source="s"><message>m</message><metadata name="n" /></trace>', "metadata requires"),
    ],
)
def test_trace_policy_shape_is_validated(policy: str, message: str) -> None:
    with pytest.raises(HTTPException, match=message):
        parse_policies_xml(f"<policies><inbound>{policy}</inbound></policies>")
