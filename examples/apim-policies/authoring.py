"""Published-port journeys for the policy overview and authoring/debug guides."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4
from xml.sax.saxutils import quoteattr

import httpx

ROOT = Path(__file__).resolve().parents[2]
TENANT = {"X-Apim-Tenant-Key": os.environ.get("APIM_TENANT_KEY", "local-dev-tenant-key")}
API = "policy-guide-authoring"
UPSTREAM = os.environ.get("APIM_UPSTREAM_BASE_URL", "http://mock-backend:8080/api").rstrip("/")


def _manage(client: httpx.Client, method: str, path: str, body=None):
    response = client.request(method, "/apim/management/" + path, headers=TENANT, json=body)
    response.raise_for_status()
    return response.json()


def _save(client: httpx.Client, xml: str, scope: str = "api/" + API) -> None:
    _manage(client, "PUT", "policies/" + scope, {"xml": xml})


def _create(client: httpx.Client) -> None:
    _manage(
        client, "PUT", "apis/" + API, {"name": "Policy guide authoring", "path": API, "upstream_base_url": UPSTREAM}
    )
    _manage(client, "PUT", f"apis/{API}/operations/echo", {"name": "Echo", "method": "POST", "url_template": "/echo"})


def _token(client: httpx.Client) -> str:
    return _manage(client, "POST", "gateways/managed/listDebugCredentials", {"apiId": API})["token"]


def _trace(client: httpx.Client, response: httpx.Response):
    return _manage(client, "POST", "gateways/managed/listTrace", {"traceId": response.headers["Apim-Trace-Id"]})


def _overview(client: httpx.Client) -> dict:
    original = _manage(client, "GET", "policies/gateway/global")["xml"]
    source = (ROOT / "tests/fixtures/policy_guides/api-management-howto-policies/example-02.xml").read_text()
    parent = '<policies><inbound><cross-domain><cross-domain-policy><allow-access-from domain="example.invalid" /></cross-domain-policy></cross-domain><find-and-replace from="initial" to="xyz" /></inbound><backend><forward-request /></backend></policies>'
    try:
        _save(client, parent, "gateway/global")
        _save(client, source)
        payload = b'{"text":"initial initial"}'
        plain = client.post(f"/{API}/echo", content=payload)
        traced = client.post(f"/{API}/echo", content=payload, headers={"Apim-Debug-Authorization": _token(client)})
        assert plain.status_code == traced.status_code == 200
        assert plain.json()["body"] == traced.json()["body"] == '{"text":"abc abc"}'
        trace = _trace(client, traced)
        assert trace["status"] == 200
        adobe = client.get("/crossdomain.xml")
        assert adobe.status_code == 200 and 'domain="example.invalid"' in adobe.text
        suppressed = source.replace("<base />", "")
        _save(client, suppressed)
        assert client.post(f"/{API}/echo", content=payload).json()["body"] == payload.decode()
        return {
            "source_transform_order": "passed",
            "base_suppression": "passed",
            "adobe_policy_file": "passed",
            "trace_body_equivalence": "passed",
        }
    finally:
        _save(client, original, "gateway/global")


def _editor(client: httpx.Client) -> dict:
    original = _manage(client, "GET", "policies/gateway/global")["xml"]
    parent = '<policies><inbound><set-header name="x-authored"><value>parent</value></set-header></inbound><backend><forward-request /></backend></policies>'
    child = '<policies><inbound><base /><set-header name="x-authored"><value>child</value></set-header></inbound></policies>'
    try:
        _save(client, parent, "gateway/global")
        _save(client, child)
        assert _manage(client, "GET", "policies/api/" + API)["xml"] == child
        effective = _manage(client, "GET", f"policies/api/{API}?effective=true")["xml"]
        assert "<base" not in effective and effective.index("parent") < effective.index("child")
        assert client.post(f"/{API}/echo").json()["headers"]["x-authored"] == "child"
        edited = child.replace("<base />", "").replace("</set-header>", "</set-header><base />")
        _save(client, edited)
        assert client.post(f"/{API}/echo").json()["headers"]["x-authored"] == "parent"
        bad = client.put("/apim/management/policies/api/" + API, headers=TENANT, json={"xml": "<policies><inbound>"})
        assert bad.status_code == 400
        assert _manage(client, "GET", "policies/api/" + API)["xml"] == edited
        specimen = Path(__file__).resolve().parents[2] / "tests/fixtures/policy_guides/set-edit-policies/example-01.xml"
        _save(client, specimen.read_text())
        assert client.post(f"/{API}/echo", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 200
        assert client.post(f"/{API}/echo", headers={"X-Forwarded-For": "198.18.0.1"}).status_code == 403
        return {
            "source_ip_allow_deny": "passed",
            "save_read_edit": "passed",
            "effective_policy_runtime_order": "passed",
            "invalid_save_preserves_policy": "passed",
        }
    finally:
        _save(client, original, "gateway/global")


def _copilot(client: httpx.Client) -> dict:
    key = "authoring-five-" + uuid4().hex
    xml = f'<policies><inbound><rate-limit-by-key calls="5" renewal-period="1" counter-key="{key}" /></inbound><outbound><set-header name="X-AspNet-Version"><value>example-version</value></set-header><set-header name="X-AspNet-Version" exists-action="delete" /></outbound></policies>'
    _save(client, xml)
    responses = [client.post(f"/{API}/echo", content=b"same") for _ in range(6)]
    assert [response.status_code for response in responses] == [200, 200, 200, 200, 200, 429]
    assert all(
        "X-AspNet-Version" not in response.headers and response.json()["body"] == "same" for response in responses[:5]
    )
    return {"authored_five_per_second": "passed", "remove_response_header": "passed"}


def _vscode(client: httpx.Client) -> dict:
    xml = f'<policies><inbound><set-backend-service base-url={quoteattr(UPSTREAM)} /><set-variable name="chosen" value="trace-value" /><set-header name="x-authored"><value>@(context.Variables["chosen"])</value></set-header></inbound></policies>'
    _save(client, xml)
    token = _token(client)
    payload = b"body survives debugging"
    plain = client.post(f"/{API}/echo", content=payload)
    traced = client.post(f"/{API}/echo", content=payload, headers={"Apim-Debug-Authorization": token})
    assert plain.status_code == traced.status_code == 200
    assert plain.json()["body"] == traced.json()["body"] == payload.decode()
    assert traced.json()["headers"]["x-authored"] == "trace-value"
    trace = _trace(client, traced)
    assert trace["upstream_url"] == UPSTREAM + "/echo"
    assert any(
        item.get("name") == "chosen" and item.get("value") == "trace-value" for item in trace["policy_variable_writes"]
    )
    assert any(item["step"] == "set-header" for item in trace["policy_steps"])
    replay = _manage(
        client,
        "POST",
        "replay",
        {
            "method": "POST",
            "path": f"/{API}/echo",
            "body_text": payload.decode(),
            "headers": {"Apim-Debug-Authorization": token},
        },
    )
    assert json.loads(replay["response"]["body_text"])["body"] == payload.decode()
    assert replay["trace"]["status"] == 200
    key = "authoring-hundred-" + uuid4().hex
    _save(
        client,
        f'<policies><inbound><rate-limit-by-key calls="100" renewal-period="60" counter-key="{key}" /></inbound></policies>',
    )
    responses = [client.post(f"/{API}/echo") for _ in range(101)]
    assert [response.status_code for response in responses[:100]] == [200] * 100
    assert responses[-1].status_code == 429
    return {
        "backend_and_header_prompt": "passed",
        "debug_variables_and_steps": "passed",
        "body_preserving_replay": "passed",
        "hundred_per_minute_prompt": "passed",
    }


def run(client: httpx.Client) -> dict:
    """Run four local guide journeys, restoring global policy and removing API."""
    _create(client)
    try:
        return {
            "overview": _overview(client),
            "set_edit": _editor(client),
            "copilot": _copilot(client),
            "vscode": _vscode(client),
        }
    finally:
        _manage(client, "DELETE", "apis/" + API)
