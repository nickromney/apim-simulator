"""In-process checks for the keyless AI gateway variant (examples/ai-gateway).

The gateway calls the sibling AI Foundry simulator with its managed identity,
a signed JWT, instead of an api-key. Foundry trusts the issuer below and
discovers its keys from the gateway's JWKS endpoint.
"""

from __future__ import annotations

import importlib.util
import json
import stat
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import httpx
import jwt
import pytest
import yaml
from fastapi.testclient import TestClient

from app.config import GatewayConfig
from app.main import create_app
from app.workload_identity import JWKS_PATH, PRIVATE_KEY_ENV

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "ai-gateway"
KEYLESS_CONFIG = EXAMPLE / "apim.foundry.keyless.json"
KEYED_CONFIG = EXAMPLE / "apim.foundry.json"
ISSUER = "http://apim-simulator:8000/.well-known/workload-identity"
RESOURCE = "https://cognitiveservices.azure.com"
MI_POLICY = f'<authentication-managed-identity resource="{RESOURCE}" />'
CHAT_PATH = "/openai/deployments/gpt-demo/chat/completions?api-version=2024-10-21"


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


key_generator = _load_module(EXAMPLE / "generate_workload_key.py", "ai_gateway_generate_workload_key")
smoke = _load_module(EXAMPLE / "smoke_foundry_keyless.py", "ai_gateway_smoke_foundry_keyless")


def _config_data(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["allowed_origins"] = ["http://localhost:8000"]
    return data


class FoundryStub:
    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        usage = {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}
        return httpx.Response(200, json={"choices": [], "usage": usage})


@pytest.fixture
def signing_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path, created = key_generator.ensure_key(tmp_path / "workload-identity")
    assert created
    monkeypatch.setenv(PRIVATE_KEY_ENV, str(path))
    return path


@pytest.fixture
def foundry() -> FoundryStub:
    return FoundryStub()


@pytest.fixture
def client(signing_key: Path, foundry: FoundryStub) -> Iterator[TestClient]:
    app = create_app(
        config=GatewayConfig.model_validate(_config_data(KEYLESS_CONFIG)),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(foundry)),
    )
    with TestClient(app) as test_client:
        yield test_client


def test_keyless_config_parses_and_carries_no_foundry_key() -> None:
    raw = KEYLESS_CONFIG.read_text(encoding="utf-8")
    assert "local-foundry-key" not in raw
    config = GatewayConfig.model_validate(_config_data(KEYLESS_CONFIG))
    identity = config.workload_identity
    assert identity.mode == "signed"
    assert identity.issuer == ISSUER
    assert identity.audience_grants == {"system-assigned": [RESOURCE]}
    for api_id in ("azure-openai-api", "content-safety-api"):
        policy = config.apis[api_id].policies_xml or ""
        assert policy.startswith(f"<policies><inbound>{MI_POLICY}")
        assert '<set-header name="api-key" exists-action="delete" />' in policy
        assert "<value>" not in policy


def test_keyless_config_differs_from_the_key_based_one_only_in_auth() -> None:
    keyed = json.loads(KEYED_CONFIG.read_text(encoding="utf-8"))
    keyless = json.loads(KEYLESS_CONFIG.read_text(encoding="utf-8"))
    assert "workload_identity" not in keyed
    assert keyless.pop("workload_identity")
    old = '<set-header name="api-key" exists-action="override"><value>local-foundry-key-alpha</value></set-header>'
    new = MI_POLICY + '<set-header name="api-key" exists-action="delete" />'
    for api_id, api in keyed["apis"].items():
        assert old in api["policies_xml"], "the key-based example must keep its api-key pairing"
        api["policies_xml"] = api["policies_xml"].replace(old, new)
        api["name"] = keyless["apis"][api_id]["name"]
    assert keyed == keyless


def test_chat_reaches_foundry_with_a_bearer_jwt_and_no_api_key(client: TestClient, foundry: FoundryStub) -> None:
    response = client.post(
        CHAT_PATH,
        headers={"api-key": "ai-team-alpha-key"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 200, response.text
    upstream = foundry.calls[-1]
    assert "api-key" not in upstream.headers
    scheme, _, token = upstream.headers["authorization"].partition(" ")
    assert scheme == "Bearer"

    jwks = client.get(JWKS_PATH).json()
    kid = jwt.get_unverified_header(token)["kid"]
    jwk = next(key for key in jwks["keys"] if key["kid"] == kid)
    claims = jwt.decode(token, jwt.PyJWK(jwk).key, algorithms=["RS256"], audience=RESOURCE, issuer=ISSUER)
    assert claims["oid"] == "system-assigned"


def test_trace_shows_managed_identity_and_api_key_removal(client: TestClient) -> None:
    response = client.post(
        CHAT_PATH,
        headers={"api-key": "ai-team-alpha-key", "x-apim-trace": "true"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    steps = client.get(f"/apim/trace/{response.headers['x-apim-trace-id']}").json()["policy_steps"]
    assert {"step": "authentication-managed-identity", "resource": RESOURCE} in steps
    assert {"step": "set-header", "name": "api-key", "action": "delete"} in steps


def test_key_generation_is_idempotent_and_container_readable(tmp_path: Path) -> None:
    directory = tmp_path / "keys"
    first, created = key_generator.ensure_key(directory)
    content = first.read_bytes()
    again, created_again = key_generator.ensure_key(directory)
    assert created and not created_again
    assert again == first and again.read_bytes() == content
    assert stat.S_IMODE(first.stat().st_mode) == 0o644


def test_smoke_garbage_jwt_names_the_trusted_issuer_but_fails_verification(signing_key: Path) -> None:
    token = smoke.garbage_jwt()
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["iss"] == ISSUER and claims["aud"] == RESOURCE
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    public_key = load_pem_private_key(signing_key.read_bytes(), password=None).public_key()
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, public_key, algorithms=["RS256"], audience=RESOURCE)


def test_overlay_mounts_the_key_read_only_and_selects_the_keyless_config() -> None:
    overlay = yaml.safe_load((EXAMPLE / "compose.foundry-keyless.yml").read_text(encoding="utf-8"))
    service = overlay["services"]["apim-simulator"]
    assert service["environment"]["APIM_CONFIG_SOURCE_PATH"] == (
        "${AI_FOUNDRY_APIM_CONFIG_PATH:-/app/examples/ai-gateway/apim.foundry.keyless.json}"
    )
    assert service["environment"][PRIVATE_KEY_ENV] == "/run/workload-identity/private.pem"
    assert service["volumes"] == ["./examples/ai-gateway/.runtime/workload-identity:/run/workload-identity:ro"]


def test_signing_key_stays_out_of_git_and_images() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "examples/ai-gateway/.runtime/" in gitignore
    assert "examples/ai-gateway/.runtime" in dockerignore


def test_root_entrypoints_delegate_to_the_example_makefile() -> None:
    root = (ROOT / "Makefile").read_text(encoding="utf-8")
    example = (EXAMPLE / "Makefile").read_text(encoding="utf-8")
    assert "up-ai-foundry-keyless:\n\t$(MAKE) --no-print-directory -C examples/ai-gateway up-foundry-keyless" in root
    assert (
        "smoke-ai-foundry-keyless:\n\t$(MAKE) --no-print-directory -C examples/ai-gateway smoke-foundry-keyless" in root
    )
    assert "-C examples/ai-gateway compose-config-foundry-keyless" in root
    assert "up-foundry-keyless: keypair" in example
    # Same project as `make up-ai-foundry`, so only one gateway answers as
    # `apim-simulator` on the shared network.
    assert "--project-name apim-simulator-ai-foundry-$(STACK_INSTANCE_SUFFIX)" in example
