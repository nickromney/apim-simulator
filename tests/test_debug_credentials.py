from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from app.config import ApiConfig, GatewayConfig, OperationConfig, TenantAccessConfig
from app.main import create_app

TENANT = {"X-Apim-Tenant-Key": "tenant"}
DEBUG = "/apim/management/gateways/managed/listDebugCredentials"


def test_debug_credentials_are_scoped_expiring_and_do_not_change_bodies(monkeypatch) -> None:
    import app.debug_credentials as debug

    received = []

    async def upstream(request):
        received.append(request)
        return httpx.Response(200, content=await request.aread(), headers={"Content-Type": "application/json"})

    cfg = GatewayConfig(
        allow_anonymous=True,
        tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"),
        apis={
            key: ApiConfig(
                name=key,
                path=key,
                upstream_base_url="http://backend",
                operations={"echo": OperationConfig(name="Echo", method="POST", url_template="/echo")},
            )
            for key in ("first", "second")
        },
    )
    with TestClient(
        create_app(config=cfg, http_client=httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    ) as client:
        assert client.post(DEBUG, json={"apiId": "first"}).status_code == 403
        assert (
            client.post(DEBUG, headers=TENANT, json={"apiId": "first", "credentialsExpireAfter": "PT2H"}).status_code
            == 422
        )
        token = client.post(DEBUG, headers=TENANT, json={"apiId": "first", "credentialsExpireAfter": "PT1S"}).json()[
            "token"
        ]
        payload = b'{"body":"preserve me"}'
        plain = client.post("/first/echo", content=payload)
        traced = client.post("/first/echo", content=payload, headers={"Apim-Debug-Authorization": token})
        assert plain.content == traced.content == payload
        trace_id = traced.headers["Apim-Trace-Id"]
        assert "apim-debug-authorization" not in received[-1].headers
        lookup = client.post("/apim/management/gateways/managed/listTrace", headers=TENANT, json={"traceId": trace_id})
        assert lookup.status_code == 200
        assert lookup.json()["status"] == 200
        wrong = client.post("/second/echo", content=payload, headers={"Apim-Debug-Authorization": token})
        assert wrong.status_code == 200
        assert "Apim-Debug-Authorization-WrongAPI" in wrong.headers
        assert "Apim-Trace-Id" not in wrong.headers
        future = debug.time.time() + 2
        monkeypatch.setattr(debug.time, "time", lambda: future)
        expired = client.post("/first/echo", content=payload, headers={"Apim-Debug-Authorization": token})
        assert expired.content == payload
        assert "Apim-Debug-Authorization-Expired" in expired.headers
        assert "Apim-Trace-Id" not in expired.headers


def test_unknown_debug_credentials_do_not_enable_tracing() -> None:
    cfg = GatewayConfig(
        allow_anonymous=True,
        apis={
            "sample": ApiConfig(
                name="Sample",
                path="sample",
                upstream_base_url="",
                operations={
                    "get": OperationConfig(
                        name="Get",
                        method="GET",
                        url_template="/",
                        policies_xml='<policies><inbound><return-response><set-status code="200" reason="OK" /></return-response></inbound></policies>',
                    )
                },
            )
        },
    )
    with TestClient(create_app(config=cfg)) as client:
        response = client.get("/sample", headers={"Apim-Debug-Authorization": "unknown"})
        assert response.status_code == 200
        assert "Apim-Debug-Authorization-Invalid" in response.headers
        assert "Apim-Trace-Id" not in response.headers
