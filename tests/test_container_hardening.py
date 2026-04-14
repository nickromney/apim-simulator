from __future__ import annotations

import os
import re
import stat
import subprocess
import tomllib
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PINNED_LGTM_IMAGE = "grafana/otel-lgtm:0.24.0@sha256:a7fbde2893d86ae4807701bc482736243e584eb90b5faa273d291ffff2a1374f"
PINNED_ACTION_REFS = {
    "actions/checkout": "de0fac2e4500dabe0009e67214ff5f5447ce83dd",
    "actions/setup-python": "a309ff8b426b58ec0e2a45f0f869d46889d02405",
    "astral-sh/setup-uv": "cec208311dfd045dd5311c1add060b2062131d57",
    "actions/setup-node": "53b83947a5a98c8d113130e565377fae1a50d02f",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "gitleaks/gitleaks-action": "ff98106e4c7b2bc287b24eaf42907196329070c7",
}


def _load_yaml(relative_path: str) -> dict:
    return yaml.safe_load((REPO_ROOT / relative_path).read_text())


def _load_toml(relative_path: str) -> dict:
    with (REPO_ROOT / relative_path).open("rb") as handle:
        return tomllib.load(handle)


def _service(compose_file: str, service_name: str) -> dict:
    compose = _load_yaml(compose_file)
    return compose["services"][service_name]


def _workflow_step_by_name(job: dict, step_name: str) -> dict:
    return next(step for step in job["steps"] if step["name"] == step_name)


def test_python_services_are_read_only_and_drop_capabilities() -> None:
    for compose_file, service_name in (
        ("compose.yml", "apim-simulator"),
        ("compose.yml", "mock-backend"),
        ("compose.mcp.yml", "mcp-server"),
        ("compose.hello.yml", "hello-api"),
        ("compose.todo.yml", "todo-api"),
        ("compose.todo.yml", "apim-simulator"),
    ):
        service = _service(compose_file, service_name)
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]


def test_nginx_services_are_read_only_and_non_root() -> None:
    for compose_file, service_name in (
        ("compose.todo.yml", "todo-frontend"),
        ("compose.edge.yml", "edge-proxy"),
        ("compose.ui.yml", "ui"),
    ):
        service = _service(compose_file, service_name)
        assert service["read_only"] is True
        assert service["user"] == "${NGINX_UID:-101}:${NGINX_GID:-101}"
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert any(tmpfs.startswith("/run/nginx:rw") for tmpfs in service["tmpfs"])


def test_non_dhi_opt_out_is_documented() -> None:
    env_example = (REPO_ROOT / ".env.example").read_text()
    assert "PYTHON_BUILD_IMAGE=python:3.13-slim" in env_example
    assert "PYTHON_RUNTIME_IMAGE=python:3.13-slim" in env_example
    assert "NGINX_RUNTIME_IMAGE=nginx:1.27-alpine" in env_example


def test_python_dockerfiles_accept_base_image_overrides() -> None:
    for relative_path in (
        "Dockerfile",
        "examples/hello-api/Dockerfile",
        "examples/todo-app/api-fastapi-container-app/Dockerfile",
        "examples/mcp-server/Dockerfile",
        "examples/mock-backend/Dockerfile",
    ):
        contents = (REPO_ROOT / relative_path).read_text()
        assert "ARG PYTHON_RUNTIME_IMAGE" in contents
        assert "HOME=/tmp" in contents


def test_uv_managed_python_dockerfiles_use_cache_mounts() -> None:
    for relative_path in (
        "Dockerfile",
        "examples/hello-api/Dockerfile",
        "examples/todo-app/api-fastapi-container-app/Dockerfile",
        "examples/mcp-server/Dockerfile",
    ):
        contents = (REPO_ROOT / relative_path).read_text()
        assert "# syntax=docker/dockerfile:1.7" in contents
        assert "RUN --mount=type=cache,target=/root/.cache/uv" in contents
        assert "uv sync --frozen" in contents
        assert "--no-cache" not in contents


def test_runtime_defaults_use_dhi_images() -> None:
    assert "ARG PYTHON_RUNTIME_IMAGE=dhi.io/python:3.13-debian13" in (REPO_ROOT / "Dockerfile").read_text()
    assert "ARG PYTHON_BUILD_IMAGE=dhi.io/python:3.13-debian13-dev" in (REPO_ROOT / "Dockerfile").read_text()
    assert "ARG NGINX_RUNTIME_IMAGE=dhi.io/nginx:1.29.5-debian13" in (REPO_ROOT / "ui/Dockerfile").read_text()
    assert (
        "ARG NGINX_RUNTIME_IMAGE=dhi.io/nginx:1.29.5-debian13"
        in (REPO_ROOT / "examples/todo-app/frontend-astro/Dockerfile").read_text()
    )


def test_ci_compose_jobs_auto_detect_dhi_auth() -> None:
    ci = _load_yaml(".github/workflows/ci.yml")
    for job_name in ("compose-config", "compose-smokes"):
        steps = ci["jobs"][job_name]["steps"]
        step = next(step for step in steps if step["name"] == "Resolve compose image defaults")
        run_script = step["run"]
        assert "scripts/check_docker_registry_auth.py" in run_script
        assert "PYTHON_BUILD_IMAGE=python:3.13-slim" in run_script
        assert "NGINX_RUNTIME_IMAGE=nginx:1.27-alpine" in run_script

    private_smoke = next(step for step in ci["jobs"]["compose-smokes"]["steps"] if step["name"] == "Private-mode smoke")
    assert "make check-private-port-clear" in private_smoke["run"]
    assert private_smoke["run"].index("make check-private-port-clear") < private_smoke["run"].index(
        "docker compose -f compose.yml -f compose.private.yml -f compose.mcp.yml up --build -d"
    )

    tutorial_smoke = next(
        step for step in ci["jobs"]["compose-smokes"]["steps"] if step["name"] == "Tutorials live smoke"
    )
    assert "make smoke-tutorials-live" in tutorial_smoke["run"]


def test_ci_uses_pinned_action_shas_and_runs_gitleaks() -> None:
    ci = _load_yaml(".github/workflows/ci.yml")
    seen_actions: set[str] = set()

    for job in ci["jobs"].values():
        for step in job.get("steps", []):
            uses = step.get("uses")
            if not uses:
                continue
            action, ref = uses.split("@", 1)
            seen_actions.add(action)
            assert re.fullmatch(r"[0-9a-f]{40}", ref), f"{uses} is not commit-pinned"
            if action in PINNED_ACTION_REFS:
                assert ref == PINNED_ACTION_REFS[action]

    assert seen_actions == set(PINNED_ACTION_REFS)

    secret_scan = ci["jobs"]["secret-scan"]
    checkout = _workflow_step_by_name(secret_scan, "Checkout")
    assert checkout["with"]["fetch-depth"] == 0

    gitleaks = _workflow_step_by_name(secret_scan, "Run gitleaks")
    assert gitleaks["uses"] == f"gitleaks/gitleaks-action@{PINNED_ACTION_REFS['gitleaks/gitleaks-action']}"
    assert gitleaks["env"]["GITLEAKS_CONFIG"] == ".gitleaks.toml"
    assert gitleaks["env"]["GITLEAKS_ENABLE_COMMENTS"] == "false"
    assert gitleaks["env"]["GITLEAKS_ENABLE_UPLOAD_ARTIFACT"] == "false"


def test_ci_runs_frontend_checks() -> None:
    ci = _load_yaml(".github/workflows/ci.yml")
    ui_job = ci["jobs"]["ui-build"]
    assert ui_job["name"] == "Frontend Checks"
    setup_node = next(step for step in ui_job["steps"] if step["name"] == "Set up Node.js")
    cache_paths = setup_node["with"]["cache-dependency-path"]
    assert "ui/package-lock.json" in cache_paths
    assert "examples/todo-app/frontend-astro/package-lock.json" in cache_paths
    run_checks = next(step for step in ui_job["steps"] if step["name"] == "Run frontend checks")
    assert run_checks["run"] == "make frontend-check"


def test_local_smoke_clients_bypass_proxy_environment() -> None:
    smoke_mcp = (REPO_ROOT / "scripts" / "smoke_mcp.py").read_text()
    smoke_edge = (REPO_ROOT / "scripts" / "smoke_edge.py").read_text()
    smoke_private = (REPO_ROOT / "scripts" / "smoke_private.py").read_text()

    assert "trust_env=False" in smoke_mcp
    assert "make_async_client(timeout=20.0, verify=VERIFY_TLS)" in smoke_edge
    assert "make_async_client(timeout=20.0)" in smoke_private


def test_generated_edge_certs_keep_server_key_readable_for_rootless_nginx(tmp_path: Path) -> None:
    subprocess.run(
        ["sh", str(REPO_ROOT / "scripts" / "gen_dev_certs.sh")],
        check=True,
        env={**os.environ, "APIM_SIMULATOR_ROOT_DIR": str(tmp_path)},
        capture_output=True,
        text=True,
    )

    cert_dir = tmp_path / "examples" / "edge" / "certs"
    server_cert_mode = stat.S_IMODE((cert_dir / "apim.localtest.me.crt").stat().st_mode)
    server_key_mode = stat.S_IMODE((cert_dir / "apim.localtest.me.key").stat().st_mode)
    ca_key_mode = stat.S_IMODE((cert_dir / "dev-root-ca.key").stat().st_mode)

    assert server_cert_mode == 0o644
    assert server_key_mode == 0o644
    assert ca_key_mode == 0o600


def test_todo_frontend_supports_runtime_image_override() -> None:
    contents = (REPO_ROOT / "examples/todo-app/frontend-astro/Dockerfile").read_text()
    assert "ARG NGINX_RUNTIME_IMAGE" in contents
    assert "runtime-config-entrypoint" in contents


def test_ui_is_built_as_a_static_hardened_container() -> None:
    service = _service("compose.ui.yml", "ui")
    assert service["read_only"] is True
    assert service["build"]["context"] == "./ui"
    assert service["ports"] == ["${OPERATOR_CONSOLE_PORT:-3007}:8080"]

    makefile = (REPO_ROOT / "Makefile").read_text()
    assert "$(COMPOSE_UI) up --build -d" in makefile


def test_private_smoke_runner_is_non_root_and_read_only() -> None:
    service = _service("compose.private.yml", "smoke-runner")
    assert service["read_only"] is True
    assert service["user"] == "${APP_UID:-65532}:${APP_GID:-65532}"
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert "entrypoint" not in service
    assert "/run/smoke:rw,exec,nosuid,nodev,uid=${APP_UID:-65532},gid=${APP_GID:-65532},mode=700" in service["tmpfs"]


def test_gateway_runtime_avoids_shell_wrappers() -> None:
    contents = (REPO_ROOT / "Dockerfile").read_text()
    assert 'CMD ["sh"' not in contents
    assert 'CMD ["/app/.venv/bin/python", "-m", "app.run_server"]' in contents


def test_management_enabled_stacks_copy_seed_config_to_tmp() -> None:
    public_service = _service("compose.yml", "apim-simulator")
    assert public_service["environment"]["APIM_CONFIG_PATH"] == "${APIM_CONFIG_PATH:-/tmp/apim-config.json}"
    assert (
        public_service["environment"]["APIM_CONFIG_SOURCE_PATH"]
        == "${APIM_CONFIG_SOURCE_PATH:-/app/examples/basic.json}"
    )

    mcp_service = _service("compose.mcp.yml", "apim-simulator")
    assert mcp_service["environment"]["APIM_CONFIG_PATH"] == "/tmp/mcp-apim-config.json"
    assert mcp_service["environment"]["APIM_CONFIG_SOURCE_PATH"] == "/app/examples/mcp/http.json"

    oidc_service = _service("compose.oidc.yml", "apim-simulator")
    assert oidc_service["environment"]["APIM_CONFIG_PATH"] == "/tmp/oidc-apim-config.json"
    assert oidc_service["environment"]["APIM_CONFIG_SOURCE_PATH"] == "/app/examples/oidc/keycloak.json"

    hello_service = _service("compose.hello.yml", "apim-simulator")
    assert hello_service["environment"]["APIM_CONFIG_PATH"] == "/tmp/hello-apim-config.json"
    assert (
        hello_service["environment"]["APIM_CONFIG_SOURCE_PATH"]
        == "${HELLO_APIM_CONFIG_PATH:-/app/examples/hello-api/apim.anonymous.json}"
    )

    todo_service = _service("compose.todo.yml", "apim-simulator")
    assert todo_service["environment"]["APIM_CONFIG_PATH"] == "/tmp/todo-apim-config.json"
    assert todo_service["environment"]["APIM_CONFIG_SOURCE_PATH"] == "/app/examples/todo-app/apim.json"


def test_mcp_build_and_smokes_use_repo_locked_dependency_flow() -> None:
    pyproject = _load_toml("pyproject.toml")
    assert pyproject["project"]["optional-dependencies"]["mcp"] == ["mcp==1.26.0"]

    mcp_service = _service("compose.mcp.yml", "mcp-server")
    assert mcp_service["build"]["context"] == "."
    assert mcp_service["build"]["dockerfile"] == "examples/mcp-server/Dockerfile"

    makefile = (REPO_ROOT / "Makefile").read_text()
    assert "UV_RUN := uv run --project $(CURDIR)" in makefile
    assert "$(UV_RUN) --extra mcp python scripts/smoke_mcp.py" in makefile
    assert "$(UV_RUN) --extra mcp python scripts/smoke_edge.py" in makefile
    assert "uv run --with mcp" not in makefile

    private_smoke_runner = (REPO_ROOT / "scripts" / "run_smoke_private.py").read_text()
    assert 'PINNED_HTTPX = "httpx==0.28.1"' in private_smoke_runner
    assert 'PINNED_MCP = "mcp==1.26.0"' in private_smoke_runner


def test_lgtm_runs_with_read_only_root() -> None:
    for compose_file in ("compose.otel.yml", "compose.todo.otel.yml"):
        service = _service(compose_file, "lgtm")
        assert service["read_only"] is True
        assert service["image"] == PINNED_LGTM_IMAGE


def test_gitleaks_config_allows_known_demo_credentials() -> None:
    config = (REPO_ROOT / ".gitleaks.toml").read_text()
    assert "[extend]" in config
    assert "useDefault = true" in config
    for marker in ("local-dev-tenant-key", "todo-demo-key", "mcp-demo-key", "tutorial-key", "demo-password"):
        assert marker in config
    assert "examples/edge/certs/apim\\.localtest\\.me\\.key" in config


def test_keycloak_persists_data_on_a_named_volume() -> None:
    service = _service("compose.oidc.yml", "keycloak")
    assert "keycloak-data:/opt/keycloak/data" in service["volumes"]
