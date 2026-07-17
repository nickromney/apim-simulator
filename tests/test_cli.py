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
    assert captured.err.strip() == "error: 403 Forbidden"


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
    assert captured.err.strip() == "error: 403 Forbidden"


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
        assert payload["api"]["upstream_base_url"] == "http://mock-backend:8080/api"
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
