from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.cli import main
from app.config import (
    ApiConfig,
    GatewayConfig,
    OperationConfig,
    ProductConfig,
    RouteConfig,
    TenantAccessConfig,
)
from app.main import create_app
from app.urls import http_url

TENANT_KEY = "local-dev-tenant-key"
BASE_URL = "http://testserver"
REPO_ROOT = Path(__file__).resolve().parents[1]


def _backend_handler(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True})


def _build_app(config: GatewayConfig):
    return create_app(config=config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(_backend_handler)))


def _management_config() -> GatewayConfig:
    return GatewayConfig(
        allow_anonymous=True,
        tenant_access=TenantAccessConfig(enabled=True, primary_key=TENANT_KEY),
        apis={
            "weather": ApiConfig(
                name="weather",
                path="weather",
                upstream_base_url=http_url("upstream"),
                products=["starter"],
                operations={
                    "current": OperationConfig(name="current", method="GET", url_template="/current"),
                },
            )
        },
        products={"starter": ProductConfig(name="Starter")},
    )


def _replay_config() -> GatewayConfig:
    return GatewayConfig(
        allow_anonymous=True,
        trace_enabled=True,
        trace_allow_unauthenticated=True,
        tenant_access=TenantAccessConfig(enabled=True, primary_key=TENANT_KEY),
        routes=[
            RouteConfig(
                name="r1",
                path_prefix="/api",
                upstream_base_url=http_url("upstream"),
                upstream_path_prefix="/api",
            )
        ],
    )


def test_status_summary_apis_products(capsys: pytest.CaptureFixture[str]) -> None:
    app = _build_app(_management_config())
    transport = httpx.ASGITransport(app=app)

    with TestClient(app):
        exit_code = main(["--base-url", BASE_URL, "--tenant-key", TENANT_KEY, "status"], transport=transport)
        assert exit_code == 0
        status_payload = json.loads(capsys.readouterr().out)
        assert status_payload["service"]["name"] == "apim-simulator"
        assert status_payload["counts"]["apis"] == 1
        assert status_payload["counts"]["products"] == 1

        exit_code = main(["--base-url", BASE_URL, "--tenant-key", TENANT_KEY, "summary"], transport=transport)
        assert exit_code == 0
        summary_payload = json.loads(capsys.readouterr().out)
        assert summary_payload["routes"][0]["name"] == "weather:current"
        assert summary_payload["gateway_policy_scope"] == {"scope_type": "gateway", "scope_name": "gateway"}

        exit_code = main(["--base-url", BASE_URL, "--tenant-key", TENANT_KEY, "apis"], transport=transport)
        assert exit_code == 0
        apis_payload = json.loads(capsys.readouterr().out)
        assert [api["id"] for api in apis_payload] == ["weather"]

        exit_code = main(["--base-url", BASE_URL, "--tenant-key", TENANT_KEY, "api", "weather"], transport=transport)
        assert exit_code == 0
        api_payload = json.loads(capsys.readouterr().out)
        assert api_payload["path"] == "weather"

        exit_code = main(
            ["--base-url", BASE_URL, "--tenant-key", TENANT_KEY, "operations", "weather"], transport=transport
        )
        assert exit_code == 0
        operations_payload = json.loads(capsys.readouterr().out)
        assert [op["id"] for op in operations_payload] == ["current"]

        exit_code = main(["--base-url", BASE_URL, "--tenant-key", TENANT_KEY, "products"], transport=transport)
        assert exit_code == 0
        products_payload = json.loads(capsys.readouterr().out)
        assert [product["id"] for product in products_payload] == ["starter"]

        exit_code = main(
            ["--base-url", BASE_URL, "--tenant-key", TENANT_KEY, "product", "starter"], transport=transport
        )
        assert exit_code == 0
        product_payload = json.loads(capsys.readouterr().out)
        assert product_payload["name"] == "Starter"

        exit_code = main(["--base-url", BASE_URL, "--tenant-key", TENANT_KEY, "subscriptions"], transport=transport)
        assert exit_code == 0
        assert json.loads(capsys.readouterr().out) == []


def test_replay_creates_a_trace_and_trace_lookup_succeeds(capsys: pytest.CaptureFixture[str]) -> None:
    app = _build_app(_replay_config())
    transport = httpx.ASGITransport(app=app)

    with TestClient(app):
        exit_code = main(
            ["--base-url", BASE_URL, "--tenant-key", TENANT_KEY, "replay", "/api/health"],
            transport=transport,
        )
        assert exit_code == 0
        replay_payload = json.loads(capsys.readouterr().out)
        assert replay_payload["response"]["status_code"] == 200
        trace_id = replay_payload["trace_id"]
        assert trace_id

        # The trace lookup is a public endpoint -- no tenant key is passed here on purpose.
        exit_code = main(["--base-url", BASE_URL, "trace", trace_id], transport=transport)
        assert exit_code == 0
        trace_payload = json.loads(capsys.readouterr().out)
        assert trace_payload["trace_id"] == trace_id

        exit_code = main(["--base-url", BASE_URL, "--tenant-key", TENANT_KEY, "traces"], transport=transport)
        assert exit_code == 0
        traces_payload = json.loads(capsys.readouterr().out)
        assert traces_payload["items"][0]["trace_id"] == trace_id


def test_trace_lookup_for_unknown_id_is_a_404_error(capsys: pytest.CaptureFixture[str]) -> None:
    app = _build_app(_replay_config())
    transport = httpx.ASGITransport(app=app)

    with TestClient(app):
        exit_code = main(["--base-url", BASE_URL, "trace", "does-not-exist"], transport=transport)

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error: 404" in captured.err


def test_replay_of_a_management_path_is_a_400_error(capsys: pytest.CaptureFixture[str]) -> None:
    app = _build_app(_replay_config())
    transport = httpx.ASGITransport(app=app)

    with TestClient(app):
        exit_code = main(
            ["--base-url", BASE_URL, "--tenant-key", TENANT_KEY, "replay", "/apim/management/status"],
            transport=transport,
        )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error: 400" in captured.err


def test_wrong_tenant_key_is_a_403_error_with_exit_code_1(capsys: pytest.CaptureFixture[str]) -> None:
    app = _build_app(_management_config())
    transport = httpx.ASGITransport(app=app)

    with TestClient(app):
        exit_code = main(
            ["--base-url", BASE_URL, "--tenant-key", "wrong-key", "status"],
            transport=transport,
        )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    # The gateway's own access log shares this stderr, so assert the CLI's line
    # is present rather than that it is the only thing written.
    assert "error: 403 Forbidden" in captured.err


def test_missing_tenant_key_is_a_403_error_with_exit_code_1(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APIM_TENANT_KEY", raising=False)
    app = _build_app(_management_config())
    transport = httpx.ASGITransport(app=app)

    with TestClient(app):
        exit_code = main(["--base-url", BASE_URL, "status"], transport=transport)

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "error: 403 Forbidden" in captured.err


def test_policy_get_and_set_round_trip(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    app = _build_app(_management_config())
    transport = httpx.ASGITransport(app=app)

    with TestClient(app):
        exit_code = main(
            ["--base-url", BASE_URL, "--tenant-key", TENANT_KEY, "policy", "api", "weather"], transport=transport
        )
        assert exit_code == 0
        initial_payload = json.loads(capsys.readouterr().out)
        assert initial_payload == {
            "scope_type": "api",
            "scope_name": "weather",
            "xml": "<policies><inbound /><backend /><outbound /><on-error /></policies>",
        }

        xml_via_flag = (
            "<policies><inbound><base /></inbound><backend><base /></backend>"
            "<outbound><base /></outbound><on-error><base /></on-error></policies>"
        )
        exit_code = main(
            [
                "--base-url",
                BASE_URL,
                "--tenant-key",
                TENANT_KEY,
                "set-policy",
                "api",
                "weather",
                "--xml",
                xml_via_flag,
            ],
            transport=transport,
        )
        assert exit_code == 0
        set_payload = json.loads(capsys.readouterr().out)
        assert set_payload["xml"] == xml_via_flag

        exit_code = main(
            ["--base-url", BASE_URL, "--tenant-key", TENANT_KEY, "policy", "api", "weather"], transport=transport
        )
        assert exit_code == 0
        assert json.loads(capsys.readouterr().out)["xml"] == xml_via_flag

        xml_via_file = (
            '<policies><inbound><set-header name="x-test" exists-action="override">'
            "<value>1</value></set-header></inbound><backend><base /></backend>"
            "<outbound><base /></outbound><on-error><base /></on-error></policies>"
        )
        policy_file = tmp_path / "policy.xml"
        policy_file.write_text(xml_via_file, encoding="utf-8")

        exit_code = main(
            [
                "--base-url",
                BASE_URL,
                "--tenant-key",
                TENANT_KEY,
                "set-policy",
                "api",
                "weather",
                "--file",
                str(policy_file),
            ],
            transport=transport,
        )
        assert exit_code == 0
        file_set_payload = json.loads(capsys.readouterr().out)
        assert file_set_payload["xml"] == xml_via_file

        exit_code = main(
            ["--base-url", BASE_URL, "--tenant-key", TENANT_KEY, "policy", "api", "weather"], transport=transport
        )
        assert exit_code == 0
        assert json.loads(capsys.readouterr().out)["xml"] == xml_via_file


def test_import_openapi_from_local_file(capsys: pytest.CaptureFixture[str]) -> None:
    app = _build_app(_management_config())
    transport = httpx.ASGITransport(app=app)
    openapi_path = REPO_ROOT / "examples" / "mock-backend" / "openapi.json"

    with TestClient(app):
        exit_code = main(
            [
                "--base-url",
                BASE_URL,
                "--tenant-key",
                TENANT_KEY,
                "import-openapi",
                "mock-backend",
                "--file",
                str(openapi_path),
                "--api-name",
                "Mock Backend",
                "--api-path",
                "mock-backend",
            ],
            transport=transport,
        )
        assert exit_code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["api"]["path"] == "mock-backend"
        # APIM selects HTTPS servers; this local sample advertises only HTTP.
        assert payload["api"]["upstream_base_url"] == ""
        assert sorted(op["id"] for op in payload["api"]["operations"]) == ["echo", "health"]
        assert payload["import"]["operation_count"] == 2


def test_put_api_round_trip(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    app = _build_app(_management_config())
    transport = httpx.ASGITransport(app=app)

    with TestClient(app):
        exit_code = main(["--base-url", BASE_URL, "--tenant-key", TENANT_KEY, "api", "weather"], transport=transport)
        assert exit_code == 0
        api_payload = json.loads(capsys.readouterr().out)

        api_file = tmp_path / "weather.json"
        api_file.write_text(json.dumps(api_payload), encoding="utf-8")

        exit_code = main(
            ["--base-url", BASE_URL, "--tenant-key", TENANT_KEY, "put-api", "weather", "--file", str(api_file)],
            transport=transport,
        )
        assert exit_code == 0
        put_payload = json.loads(capsys.readouterr().out)
        assert put_payload["path"] == api_payload["path"]
        assert put_payload["upstream_base_url"] == api_payload["upstream_base_url"]
        assert [op["id"] for op in put_payload["operations"]] == ["current"]


def test_delete_api_requires_yes_flag(capsys: pytest.CaptureFixture[str]) -> None:
    app = _build_app(_management_config())
    transport = httpx.ASGITransport(app=app)

    with TestClient(app):
        exit_code = main(
            ["--base-url", BASE_URL, "--tenant-key", TENANT_KEY, "delete-api", "weather"], transport=transport
        )
        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "error: refusing to delete API 'weather' without --yes" in captured.err

        exit_code = main(
            ["--base-url", BASE_URL, "--tenant-key", TENANT_KEY, "delete-api", "weather", "--yes"],
            transport=transport,
        )
        assert exit_code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload == {"deleted": True, "api_id": "weather", "remaining": 0}

        exit_code = main(["--base-url", BASE_URL, "--tenant-key", TENANT_KEY, "api", "weather"], transport=transport)
        assert exit_code == 1
        assert "error: 404" in capsys.readouterr().err


def test_put_api_file_errors_are_reported_clearly(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    app = _build_app(_management_config())
    transport = httpx.ASGITransport(app=app)

    with TestClient(app):
        missing_path = tmp_path / "missing.json"
        exit_code = main(
            ["--base-url", BASE_URL, "--tenant-key", TENANT_KEY, "put-api", "weather", "--file", str(missing_path)],
            transport=transport,
        )
        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert f"error: could not read {missing_path}" in captured.err

        invalid_json_path = tmp_path / "invalid.json"
        invalid_json_path.write_text("{not valid json", encoding="utf-8")
        exit_code = main(
            [
                "--base-url",
                BASE_URL,
                "--tenant-key",
                TENANT_KEY,
                "put-api",
                "weather",
                "--file",
                str(invalid_json_path),
            ],
            transport=transport,
        )
        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert f"error: invalid JSON in {invalid_json_path}" in captured.err


def test_put_product_and_delete_product_round_trip(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    app = _build_app(_management_config())
    transport = httpx.ASGITransport(app=app)

    with TestClient(app):
        exit_code = main(
            ["--base-url", BASE_URL, "--tenant-key", TENANT_KEY, "product", "starter"], transport=transport
        )
        assert exit_code == 0
        product_payload = json.loads(capsys.readouterr().out)

        product_file = tmp_path / "starter.json"
        product_file.write_text(json.dumps(product_payload), encoding="utf-8")

        exit_code = main(
            [
                "--base-url",
                BASE_URL,
                "--tenant-key",
                TENANT_KEY,
                "put-product",
                "starter",
                "--file",
                str(product_file),
            ],
            transport=transport,
        )
        assert exit_code == 0
        put_payload = json.loads(capsys.readouterr().out)
        assert put_payload["name"] == product_payload["name"]
        assert put_payload["state"] == product_payload["state"]

        exit_code = main(
            ["--base-url", BASE_URL, "--tenant-key", TENANT_KEY, "delete-product", "starter"], transport=transport
        )
        assert exit_code == 1
        assert "without --yes" in capsys.readouterr().err

        exit_code = main(
            ["--base-url", BASE_URL, "--tenant-key", TENANT_KEY, "delete-product", "starter", "--yes"],
            transport=transport,
        )
        assert exit_code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload == {"deleted": True, "product_id": "starter", "remaining": 0}


def test_unknown_subcommand_is_an_argparse_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["not-a-real-command"])

    assert exc_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_missing_command_is_an_argparse_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main([])

    assert exc_info.value.code == 2


def test_offline_discovery_and_preview_never_contact_server(capsys: pytest.CaptureFixture[str]) -> None:
    def reject_request(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"offline command sent {request.method} {request.url}")

    transport = httpx.MockTransport(reject_request)
    assert main(["commands"], transport=transport) == 0
    catalog = json.loads(capsys.readouterr().out)
    assert catalog["schema_version"] == 1
    assert catalog["commands"]["replay"]["effect"] == "execute"
    assert catalog["commands"]["delete-api"]["requires_confirmation"] is True
    assert main(["--tenant-key", "secret-test-key", "--dry-run", "delete-api", "weather"], transport=transport) == 0
    output = capsys.readouterr().out
    assert "secret-test-key" not in output
    plan = json.loads(output)
    assert plan["base_url"] == "http://localhost:8000"
    assert plan["method"] == "DELETE"
    assert plan["path"] == "/apim/management/apis/weather"
    assert plan["requires_confirmation"] is True


def test_preview_matches_execution_request(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    payload = {"name": "Weather", "path": "weather"}
    source = tmp_path / "api.json"
    source.write_text(json.dumps(payload))
    args = ["put-api", "weather", "--file", str(source)]
    assert main(["--dry-run", *args]) == 0
    plan = json.loads(capsys.readouterr().out)
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    assert main(args, transport=httpx.MockTransport(handler)) == 0
    request = requests[0]
    assert request.method == plan["method"]
    assert request.url.path == plan["path"]
    assert json.loads(request.content) == plan["body"] == payload


def test_preview_reports_missing_payload_file(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert main(["--dry-run", "put-api", "weather", "--file", str(tmp_path / "missing.json")]) == 1
    assert "could not read" in capsys.readouterr().err


def test_effective_policy_cli_uses_product_context(capsys: pytest.CaptureFixture[str]) -> None:
    app = _build_app(_management_config())
    with TestClient(app):
        assert (
            main(
                [
                    "--base-url",
                    BASE_URL,
                    "--tenant-key",
                    TENANT_KEY,
                    "policy",
                    "--effective",
                    "--product-id",
                    "starter",
                    "api",
                    "weather",
                ],
                transport=httpx.ASGITransport(app=app),
            )
            == 0
        )
    result = json.loads(capsys.readouterr().out)
    assert result["effective"] is True
    assert result["product_id"] == "starter"
    assert "<policies>" in result["xml"]


@pytest.mark.parametrize("preview", [[], ["--dry-run"]])
def test_product_context_requires_effective_policy(preview: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main([*preview, "policy", "--product-id", "starter", "api", "weather"]) == 1
    assert "--product-id requires --effective" in capsys.readouterr().err


def test_preview_omits_url_credentials(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--base-url", "http://operator:password@localhost:8000", "--dry-run", "status"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["base_url"] == "http://localhost:8000"


def test_inspect_collects_metadata_from_application(capsys: pytest.CaptureFixture[str]) -> None:
    app = _build_app(_management_config())
    with TestClient(app):
        assert (
            main(
                ["--base-url", BASE_URL, "--tenant-key", TENANT_KEY, "inspect"], transport=httpx.ASGITransport(app=app)
            )
            == 0
        )
    report = json.loads(capsys.readouterr().out)
    assert report["schema_version"] == 1
    assert report["atomic"] is False
    assert report["complete"] is True
    assert report["observations"]["apis"]["body"][0]["id"] == "weather"
    assert set(report["observations"]) == {"health", "status", "service", "apis", "products"}


def test_inspect_preserves_denied_access_without_hiding_health(capsys: pytest.CaptureFixture[str]) -> None:
    app = _build_app(_management_config())
    with TestClient(app):
        assert (
            main(
                ["--base-url", BASE_URL, "--tenant-key", "invalid-key", "inspect"],
                transport=httpx.ASGITransport(app=app),
            )
            == 1
        )
    report = json.loads(capsys.readouterr().out)
    assert report["complete"] is False
    assert report["observations"]["health"]["status_code"] == 200
    assert report["observations"]["apis"]["status_code"] == 403


def test_inspect_preserves_transport_and_non_json_failures(capsys: pytest.CaptureFixture[str]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/status"):
            raise httpx.ConnectError("secret-test-key", request=request)
        if request.url.path.endswith("/service"):
            return httpx.Response(502, text="backend unavailable")
        return httpx.Response(200, json={"ok": True})

    assert main(["--tenant-key", "secret-test-key", "inspect"], transport=httpx.MockTransport(handler)) == 1
    output = capsys.readouterr().out
    assert "secret-test-key" not in output
    report = json.loads(output)
    assert report["complete"] is False
    assert report["observations"]["status"]["error"] == "transport_error"
    assert report["observations"]["service"]["body"] == "backend unavailable"
    assert report["observations"]["products"]["status_code"] == 200


def test_inspect_preview_is_offline(capsys: pytest.CaptureFixture[str]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("preview contacted server")

    assert main(["--dry-run", "inspect"], transport=httpx.MockTransport(handler)) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["atomic"] is False
    assert len(plan["requests"]) == 5
    assert all(request["method"] == "GET" for request in plan["requests"])


def test_bearer_token_sends_only_authorization_for_management_and_inspection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("APIM_TENANT_KEY", "ambient-key")
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    assert main(["--bearer-token", "scoped-token", "status"], transport=transport) == 0
    assert main(["--bearer-token", "scoped-token", "inspect"], transport=transport) == 0
    assert requests[0].headers["authorization"] == "Bearer scoped-token"
    assert "x-apim-tenant-key" not in requests[0].headers
    assert "authorization" not in requests[1].headers
    for request in requests[2:]:
        assert request.headers["authorization"] == "Bearer scoped-token"
        assert "x-apim-tenant-key" not in request.headers
    assert "scoped-token" not in capsys.readouterr().out


def test_bearer_environment_and_explicit_tenant_precedence(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("APIM_BEARER_TOKEN", "environment-token")
    monkeypatch.delenv("APIM_TENANT_KEY", raising=False)
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(403, json={"detail": "environment-token rejected"})

    transport = httpx.MockTransport(handler)
    assert main(["status"], transport=transport) == 1
    assert requests[-1].headers["authorization"] == "Bearer environment-token"
    assert "environment-token" not in capsys.readouterr().err
    assert main(["--tenant-key", "explicit-key", "status"], transport=transport) == 1
    assert requests[-1].headers["x-apim-tenant-key"] == "explicit-key"
    assert "authorization" not in requests[-1].headers


def test_conflicting_credentials_fail_without_disclosure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("APIM_TENANT_KEY", "secret-key")
    monkeypatch.setenv("APIM_BEARER_TOKEN", "secret-token")
    assert main(["--dry-run", "status"]) == 0
    assert main(["commands"]) == 0
    output = capsys.readouterr().out
    assert "secret-key" not in output and "secret-token" not in output
    with pytest.raises(SystemExit):
        main(["status"])
    assert "secret-key" not in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["--tenant-key", "secret-key", "--bearer-token", "secret-token", "status"])
    errors = capsys.readouterr().err
    assert "secret-key" not in errors and "secret-token" not in errors
    assert main(["--bearer-token", "secret-token", "--dry-run", "status"]) == 0
    assert "secret-token" not in capsys.readouterr().out


def test_inspect_redacts_escaped_credentials_in_error_body(capsys: pytest.CaptureFixture[str]) -> None:
    secret = 'abc"def'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": [secret], secret: "denied"})

    assert main(["--tenant-key", secret, "inspect"], transport=httpx.MockTransport(handler)) == 1
    output = capsys.readouterr().out
    assert secret not in output
    assert json.loads(output)["observations"]["status"]["body"] == {"detail": ["[redacted]"], "[redacted]": "denied"}


@pytest.mark.parametrize("token", ["tökén", "line\nbreak", ""])
def test_invalid_bearer_token_fails_without_disclosure(token: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--bearer-token", token, "status"]) == 1
    output = capsys.readouterr()
    assert "visible ASCII" in output.err
    if token:
        assert token not in output.err


@pytest.mark.parametrize("flag", ["--tenant-key", "--bearer-token", "--tenant-k", "--bearer-t"])
@pytest.mark.parametrize("inline", [False, True])
def test_misplaced_credential_option_does_not_echo_secret(
    flag: str, inline: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    args = [f"{flag}=sensitive-token"] if inline else [flag, "sensitive-token"]
    with pytest.raises(SystemExit):
        main(["apis", *args])
    error = capsys.readouterr().err
    assert "credential options must precede the command" in error
    assert "sensitive-token" not in error


def test_policy_plan_and_guarded_apply(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    source = tmp_path / "policy.xml"
    source.write_text("<policies />", encoding="utf-8")
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"etag": '"v1"', "diff": "change"})
        return httpx.Response(412, json={"detail": "policy changed"})

    transport = httpx.MockTransport(handler)
    args = ["weather", "--file", str(source)]
    assert main(["--bearer-token", "token", "plan-policy", *args], transport=transport) == 0
    assert json.loads(capsys.readouterr().out)["etag"] == '"v1"'
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/apim/management/policies/api/weather"
    assert requests[0].headers["authorization"] == "Bearer token"
    assert json.loads(requests[0].content) == {"xml": "<policies />"}
    apply = ["apply-policy", *args, "--if-match", '"v1"']
    assert main(["--dry-run", *apply], transport=transport) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["headers"] == {"If-Match": '"v1"'}
    assert main(apply, transport=transport) == 1
    assert "412 policy changed" in capsys.readouterr().err
    assert requests[1].headers["if-match"] == plan["headers"]["If-Match"]


def test_api_artifact_commands(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    artifact = {
        "schema_version": 1,
        "openapi": {"openapi": "3.0.0", "paths": {"/z": {"get": {}}, "/a": {"get": {}}}},
        "api_policy": None,
        "operation_policies": {},
    }
    source = tmp_path / "api.json"
    source.write_text(json.dumps(artifact), encoding="utf-8")
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"api_id": "weather", "etag": '"v1"', "diff": {}})
        return httpx.Response(200, json=artifact)

    transport = httpx.MockTransport(handler)
    assert main(["--tenant-key", "secret", "extract-api", "weather"], transport=transport) == 0
    extracted = capsys.readouterr().out
    assert json.loads(extracted) == artifact
    assert extracted == json.dumps(artifact, indent=2) + "\n"
    assert list(json.loads(extracted)["openapi"]["paths"]) == ["/z", "/a"]
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/apim/management/apis/weather/artifacts"
    args = ["weather", "--file", str(source), "--upstream-base-url", "https://example.test"]
    assert main(["plan-api", *args], transport=transport) == 0
    assert json.loads(capsys.readouterr().out)["etag"] == '"v1"'
    assert requests[1].method == "POST"
    assert json.loads(requests[1].content) == {"artifact": artifact, "upstream_base_url": "https://example.test"}
    apply = ["apply-api", "weather", "--file", str(source), "--if-match", '"v1"']
    assert main(["--dry-run", *apply], transport=transport) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["headers"] == {"If-Match": '"v1"'}
    assert preview["body"] == {"artifact": artifact}
    assert main(apply, transport=transport) == 0
    assert json.loads(capsys.readouterr().out) == artifact
    assert requests[2].method == "PUT"
    assert requests[2].headers["if-match"] == '"v1"'
    assert json.loads(requests[2].content) == preview["body"]


def test_api_artifact_invalid_input_and_catalog(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    source = tmp_path / "api.json"
    source.write_text("[]", encoding="utf-8")
    assert main(["--dry-run", "plan-api", "weather", "--file", str(source)]) == 1
    assert "must contain a JSON object" in capsys.readouterr().err
    source.write_text("{}", encoding="utf-8")
    assert main(["apply-api", "weather", "--file", str(source), "--if-match", "*"]) == 1
    assert "specific strong quoted ETag" in capsys.readouterr().err
    assert main(["commands"]) == 0
    commands = json.loads(capsys.readouterr().out)["commands"]
    assert commands["extract-api"]["effect"] == commands["plan-api"]["effect"] == "read"
    assert commands["apply-api"]["effect"] == "write"


@pytest.mark.parametrize("etag", ["*", 'W/"v1"', "v1", '"a", "b"', '"a\nb"'])
def test_apply_policy_rejects_non_specific_etag(etag: str, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    source = tmp_path / "policy.xml"
    source.write_text("<policies />", encoding="utf-8")
    for preview in ([], ["--dry-run"]):
        assert main([*preview, "apply-policy", "weather", "--file", str(source), "--if-match", etag]) == 1
        assert "specific strong quoted ETag" in capsys.readouterr().err


def test_product_artifact_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    artifact = tmp_path / "artifact.json"
    overrides = tmp_path / "overrides.json"
    artifact.write_text('{"z": 1, "a": 2}')
    overrides.write_text('{"named_values": {"token": "secret-value"}}')
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"diff": "reviewed", "override_ids": ["token"]})

    transport = httpx.MockTransport(handler)
    assert main(["extract-product", "starter"], transport=transport) == 0
    capsys.readouterr()
    assert requests[-1].method == "GET"
    assert requests[-1].url.path == "/apim/management/products/starter/artifacts"
    for command, method in (("plan-product", "POST"), ("apply-product", "PUT")):
        args = ["--tenant-key", TENANT_KEY, command, "starter", "--file", str(artifact), "--overrides", str(overrides)]
        if command == "apply-product":
            args += ["--if-match", '"etag"']
        assert main(args, transport=transport) == 0
        assert json.loads(capsys.readouterr().out)["diff"] == "reviewed"
        request = requests[-1]
        assert request.method == method
        assert request.headers["X-Apim-Tenant-Key"] == TENANT_KEY
        assert list(json.loads(request.content)["artifact"]) == ["z", "a"]
        assert json.loads(request.content)["overrides"]["named_values"]["token"] == "secret-value"
        if command == "apply-product":
            assert request.headers["If-Match"] == '"etag"'

    assert main(["--dry-run", "plan-product", "starter", "--file", str(artifact), "--overrides", str(overrides)]) == 0
    preview = capsys.readouterr().out
    assert "secret-value" not in preview
    assert json.loads(preview)["body"]["overrides"] == "[redacted]"


def test_product_artifact_error_does_not_echo_overrides(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    artifact = tmp_path / "artifact.json"
    overrides = tmp_path / "overrides.json"
    artifact.write_text("{}")
    overrides.write_text('{"named_values": {"token": "secret-value"}}')
    transport = httpx.MockTransport(lambda _: httpx.Response(422, json={"detail": [{"input": "secret-value"}]}))
    assert (
        main(["plan-product", "starter", "--file", str(artifact), "--overrides", str(overrides)], transport=transport)
        == 1
    )
    assert "secret-value" not in capsys.readouterr().err


def test_product_artifact_error_shows_safe_dependency_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    artifact = tmp_path / "artifact.json"
    overrides = tmp_path / "overrides.json"
    artifact.write_text('{"backends": {"orders": {}}}')
    overrides.write_text('{"named_values": {"token": "secret-value"}}')
    args = ["plan-product", "starter", "--file", str(artifact), "--overrides", str(overrides)]
    safe = httpx.MockTransport(lambda _: httpx.Response(400, json={"detail": "Backend orders requires an override"}))
    assert main(args, transport=safe) == 1
    assert "Backend orders requires an override" in capsys.readouterr().err
    malicious = httpx.MockTransport(lambda _: httpx.Response(400, json={"detail": "secret-value"}))
    assert main(args, transport=malicious) == 1
    assert "secret-value" not in capsys.readouterr().err


def test_service_artifact_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    artifact = tmp_path / "service.json"
    overrides = tmp_path / "overrides.json"
    artifact.write_text('{"apis": {"orders": {}}}')
    overrides.write_text('{"named_values": {"token": "secret-value"}}')
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"override_ids": ["token"]})

    transport = httpx.MockTransport(handler)
    assert main(["extract-service"], transport=transport) == 0
    capsys.readouterr()
    assert requests[-1].method == "GET"
    assert requests[-1].url.path == "/apim/management/service/artifacts"
    for command, method in (("plan-service", "POST"), ("apply-service", "PUT")):
        args = [command, "--file", str(artifact), "--overrides", str(overrides)]
        if command == "apply-service":
            args += ["--if-match", '"etag"']
        assert main(args, transport=transport) == 0
        assert json.loads(capsys.readouterr().out) == {"override_ids": ["token"]}
        request = requests[-1]
        assert request.method == method
        assert request.url.path == "/apim/management/service/artifacts"
        assert json.loads(request.content) == {
            "artifact": {"apis": {"orders": {}}},
            "overrides": {"named_values": {"token": "secret-value"}},
        }
        if command == "apply-service":
            assert request.headers["If-Match"] == '"etag"'
        assert main(["--dry-run", *args], transport=transport) == 0
        preview = capsys.readouterr().out
        assert "secret-value" not in preview
        assert json.loads(preview)["body"]["overrides"] == "[redacted]"


def test_service_artifact_errors_hide_override_values(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    artifact = tmp_path / "service.json"
    overrides = tmp_path / "overrides.json"
    artifact.write_text('{"backends": {"orders": {}}}')
    overrides.write_text('{"named_values": {"token": "secret-value"}}')
    args = ["plan-service", "--file", str(artifact), "--overrides", str(overrides)]
    for status, detail in ((422, [{"input": "secret-value"}]), (400, "secret-value")):
        transport = httpx.MockTransport(
            lambda _, status=status, detail=detail: httpx.Response(status, json={"detail": detail})
        )
        assert main(args, transport=transport) == 1
        assert "secret-value" not in capsys.readouterr().err
    transport = httpx.MockTransport(
        lambda _: httpx.Response(400, json={"detail": "Backend orders requires an override"})
    )
    assert main(args, transport=transport) == 1
    assert "Backend orders requires an override" in capsys.readouterr().err


def test_service_artifact_error_shows_safe_service_message(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    artifact = tmp_path / "service.json"
    artifact.write_text("{}")
    message = "API product links must reference bundle products"
    transport = httpx.MockTransport(lambda _: httpx.Response(400, json={"detail": message}))
    assert main(["plan-service", "--file", str(artifact)], transport=transport) == 1
    assert message in capsys.readouterr().err
