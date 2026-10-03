"""Local management equivalents of the authoring and editor/debug guides.

Source links and sample provenance are recorded in docs/policy-validation.
These checks assert authored behavior and trace/replay results; they do not
claim to run Azure Copilot, VS Code, or the Azure debugging transport.
"""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from app.config import GatewayConfig, TenantAccessConfig
from app.main import create_app

TENANT = {"X-Apim-Tenant-Key": "tenant"}
ADMIN = "/apim/management"


def _author_api(client: TestClient) -> None:
    assert (
        client.put(
            ADMIN + "/apis/authored",
            headers=TENANT,
            json={"name": "Authored", "path": "authored", "upstream_base_url": "http://backend"},
        ).status_code
        == 200
    )
    assert (
        client.put(
            ADMIN + "/apis/authored/operations/echo",
            headers=TENANT,
            json={"name": "Echo", "method": "POST", "url_template": "/echo"},
        ).status_code
        == 200
    )


def _client() -> TestClient:
    def backend(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=request.content,
            headers={
                "Content-Type": "text/plain",
                "X-AspNet-Version": "example-version",
                "x-received": request.headers.get("x-authored", "none"),
            },
        )

    return TestClient(
        create_app(
            config=GatewayConfig(
                allow_anonymous=True, tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant")
            ),
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(backend)),
        )
    )


def test_editor_save_read_edit_effective_policy_and_invalid_save_keeps_previous_policy() -> None:
    with _client() as client:
        _author_api(client)
        parent = '<policies><inbound><set-header name="x-authored"><value>parent</value></set-header></inbound><backend><forward-request /></backend></policies>'
        child = '<policies><inbound><base /><set-header name="x-authored"><value>child</value></set-header></inbound></policies>'
        endpoint = ADMIN + "/policies/api/authored"
        assert client.put(ADMIN + "/policies/gateway/global", headers=TENANT, json={"xml": parent}).status_code == 200
        assert client.put(endpoint, headers=TENANT, json={"xml": child}).status_code == 200
        assert client.get(endpoint, headers=TENANT).json()["xml"] == child
        effective = client.get(endpoint, headers=TENANT, params={"effective": True}).json()["xml"]
        assert "<base" not in effective
        assert effective.index("parent") < effective.index("child")
        assert client.post("/authored/echo", content=b"same").headers["x-received"] == "child"
        edited = child.replace("<base /><set-header", "<set-header").replace(
            "</set-header></inbound>", "</set-header><base /></inbound>"
        )
        assert client.put(endpoint, headers=TENANT, json={"xml": edited}).status_code == 200
        assert client.post("/authored/echo", content=b"same").headers["x-received"] == "parent"
        bad = client.put(endpoint, headers=TENANT, json={"xml": "<policies><inbound>"})
        assert bad.status_code == 400
        assert client.get(endpoint, headers=TENANT).json()["xml"] == edited
        assert client.delete(ADMIN + "/apis/authored", headers=TENANT).status_code == 200
        assert client.get(endpoint, headers=TENANT).status_code == 404


def test_copilot_prompt_outcomes_remove_response_header_and_limit_five_calls_per_second() -> None:
    # Local policies authored from the guide's two prompts; no AI service call.
    policy = '<policies><inbound><rate-limit-by-key calls="5" renewal-period="1" counter-key="authoring-example" /></inbound><outbound><set-header name="X-AspNet-Version" exists-action="delete" /></outbound></policies>'
    with _client() as client:
        _author_api(client)
        assert client.put(ADMIN + "/policies/api/authored", headers=TENANT, json={"xml": policy}).status_code == 200
        responses = [client.post("/authored/echo", content=b"preserved") for _ in range(6)]
        assert [r.status_code for r in responses] == [200, 200, 200, 200, 200, 429]
        assert all(r.content == b"preserved" and "X-AspNet-Version" not in r.headers for r in responses[:5])


def test_debug_scoped_credentials_effective_steps_variables_and_replay_keep_body_results() -> None:
    policy = '<policies><inbound><set-variable name="chosen" value="trace-value" /><set-header name="x-authored"><value>@(context.Variables["chosen"])</value></set-header></inbound></policies>'
    with _client() as client:
        _author_api(client)
        assert (
            client.put(ADMIN + "/policies/operation/authored:echo", headers=TENANT, json={"xml": policy}).status_code
            == 200
        )
        token = client.post(
            ADMIN + "/gateways/managed/listDebugCredentials", headers=TENANT, json={"apiId": "authored"}
        ).json()["token"]
        payload = b"unchanged debug body"
        plain = client.post("/authored/echo", content=payload)
        traced = client.post("/authored/echo", content=payload, headers={"Apim-Debug-Authorization": token})
        assert plain.content == traced.content == payload
        assert plain.headers["x-received"] == traced.headers["x-received"] == "trace-value"
        trace_id = traced.headers["Apim-Trace-Id"]
        lookup = client.post(ADMIN + "/gateways/managed/listTrace", headers=TENANT, json={"traceId": trace_id})
        assert lookup.status_code == 200
        trace = lookup.json()
        replay = client.post(
            ADMIN + "/replay",
            headers=TENANT,
            json={
                "method": "POST",
                "path": "/authored/echo",
                "body_text": payload.decode(),
                "headers": {"Apim-Debug-Authorization": token},
            },
        )
        assert replay.status_code == 200
        assert replay.json()["response"]["body_text"] == payload.decode()
        assert replay.json()["response"]["headers"]["x-received"] == "trace-value"
        assert trace["status"] == replay.json()["trace"]["status"] == 200
        assert any(
            item.get("name") == "chosen" and item.get("value") == "trace-value"
            for item in trace["policy_variable_writes"]
        )
        assert [item["step"] for item in trace["policy_steps"]][:2] == ["set-header", "forward-request"]


def test_vscode_backend_and_header_prompt_routes_to_authored_backend() -> None:
    policy = '<policies><inbound><set-backend-service base-url="http://authored-backend" /><set-header name="x-authored"><value>custom</value></set-header></inbound></policies>'
    with _client() as client:
        _author_api(client)
        assert client.put(ADMIN + "/policies/api/authored", headers=TENANT, json={"xml": policy}).status_code == 200
        token = client.post(
            ADMIN + "/gateways/managed/listDebugCredentials", headers=TENANT, json={"apiId": "authored"}
        ).json()["token"]
        response = client.post("/authored/echo", content=b"backend prompt", headers={"Apim-Debug-Authorization": token})
        assert response.content == b"backend prompt"
        assert response.headers["x-received"] == "custom"
        trace = client.post(
            ADMIN + "/gateways/managed/listTrace", headers=TENANT, json={"traceId": response.headers["Apim-Trace-Id"]}
        ).json()
        assert trace["upstream_url"] == "http://authored-backend/echo"


def test_vscode_hundred_calls_per_minute_prompt_enforces_the_authored_limit() -> None:
    policy = '<policies><inbound><rate-limit-by-key calls="100" renewal-period="60" counter-key="vscode-authoring-example" /></inbound></policies>'
    with _client() as client:
        _author_api(client)
        assert client.put(ADMIN + "/policies/api/authored", headers=TENANT, json={"xml": policy}).status_code == 200
        responses = [client.post("/authored/echo", content=b"same") for _ in range(101)]
        assert [r.status_code for r in responses[:100]] == [200] * 100
        assert all(r.content == b"same" for r in responses[:100])
        assert responses[-1].status_code == 429
        assert 0 < int(responses[-1].headers["retry-after"]) <= 60


def test_published_port_authoring_runner_assertions_exercise_gateway_and_restore_global_policy() -> None:
    import runpy

    run = runpy.run_path("examples/apim-policies/authoring.py")["run"]
    from app.config import DEFAULT_GLOBAL_POLICY_XML

    def backend(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"body": request.content.decode(), "headers": dict(request.headers)})

    cfg = GatewayConfig(
        network_security={"allow_simulated_forwarded_headers": True},
        allow_anonymous=True,
        tenant_access=TenantAccessConfig(enabled=True, primary_key="local-dev-tenant-key"),
    )
    app = create_app(config=cfg, http_client=httpx.AsyncClient(transport=httpx.MockTransport(backend)))
    with TestClient(app) as client:
        results = run(client)
        assert set(results) == {"overview", "set_edit", "copilot", "vscode"}
        assert results["vscode"]["body_preserving_replay"] == "passed"
        assert app.state.gateway_config.policies_xml == DEFAULT_GLOBAL_POLICY_XML
        assert "policy-guide-authoring" not in app.state.gateway_config.apis


def test_read_save_global_policy_preserves_default_forwarding_but_not_explicit_suppression() -> None:
    from app.config import DEFAULT_GLOBAL_POLICY_XML

    with _client() as client:
        _author_api(client)
        endpoint = ADMIN + "/policies/gateway/global"
        original = client.get(endpoint, headers=TENANT).json()["xml"]
        assert original == DEFAULT_GLOBAL_POLICY_XML
        assert client.put(endpoint, headers=TENANT, json={"xml": original}).status_code == 200
        assert client.post("/authored/echo", content=b"still forwarded").content == b"still forwarded"
        explicit_empty = "<policies><inbound /><backend /><outbound /><on-error /></policies>"
        assert client.put(endpoint, headers=TENANT, json={"xml": explicit_empty}).status_code == 200
        assert client.get(endpoint, headers=TENANT).json()["xml"] == explicit_empty
        assert client.post("/authored/echo", content=b"not forwarded").content == b""
