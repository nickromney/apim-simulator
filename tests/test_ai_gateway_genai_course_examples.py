"""In-process checks for the AI gateway examples that follow the APIM GenAI patterns.

- PTU first, pay-as-you-go spillover: a 429 with Retry-After from the
  provisioned deployment opens its breaker and the pool retries on the
  priority-2 member (examples/ai-gateway/apim.json).
- Gateway semantic caching against the AI Foundry simulator's embeddings
  deployment (examples/ai-gateway/apim.foundry.json).
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import GatewayConfig
from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "ai-gateway"
HEADERS = {"api-key": "ai-team-alpha-key"}


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _gateway(config_file: str, handler) -> TestClient:
    data = json.loads((EXAMPLE / config_file).read_text(encoding="utf-8"))
    data["allowed_origins"] = ["http://localhost:8000"]
    config = GatewayConfig.model_validate(data)
    return TestClient(create_app(config=config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))))


def _completion(text: str) -> httpx.Response:
    usage = {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}], "usage": usage})


def test_ptu_pool_spills_to_paygo_on_429_and_stays_there() -> None:
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host == "llm-backend-ptu":
            if hosts.count("llm-backend-ptu") > 2:
                return httpx.Response(429, headers={"retry-after": "45"}, json={"error": {"code": "429"}})
            return _completion("ptu")
        return _completion("paygo")

    path = "/ptu/openai/deployments/gpt-demo/chat/completions?api-version=2024-10-21"
    body = {"messages": [{"role": "user", "content": "hi"}]}
    with _gateway("apim.json", handler) as client:
        responses = [client.post(path, headers=HEADERS, json=body) for _ in range(5)]

    assert [response.status_code for response in responses] == [200] * 5
    assert [response.headers["x-apim-backend-id"] for response in responses] == [
        "llm-backend-ptu",
        "llm-backend-ptu",
        "llm-backend-b",
        "llm-backend-b",
        "llm-backend-b",
    ]
    # The third call reached PTU once, got 429, and was retried on pay-as-you-go;
    # later calls skip PTU while its breaker honours Retry-After.
    assert hosts == [
        "llm-backend-ptu",
        "llm-backend-ptu",
        "llm-backend-ptu",
        "llm-backend-b",
        "llm-backend-b",
        "llm-backend-b",
    ]


def test_mock_deployment_answers_429_with_retry_after_over_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOCK_LLM_TOKENS_PER_MINUTE", "100")
    backend = _load_module(ROOT / "examples" / "llm-backend" / "main.py", "llm_backend_capacity")
    body = {"messages": [{"role": "user", "content": "Use some provisioned capacity please."}]}
    with TestClient(backend.app) as client:
        statuses = []
        for _ in range(5):
            response = client.post("/openai/deployments/gpt-demo/chat/completions", json=body)
            statuses.append(response.status_code)
            if response.status_code == 429:
                break

    assert statuses[0] == 200
    assert statuses[-1] == 429
    assert 1 <= int(response.headers["retry-after"]) <= 60
    assert response.json()["error"]["code"] == "429"


def test_foundry_example_serves_reworded_prompt_from_gateway_semantic_cache() -> None:
    model_calls: list[httpx.Request] = []
    embedding_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/embeddings"):
            embedding_calls.append(request)
            words = re.findall(r"[a-z]+", json.loads(request.content)["input"].lower())
            vocab = ("what", "is", "the", "time", "off", "policy", "capital", "france")
            return httpx.Response(200, json={"data": [{"embedding": [float(words.count(w)) for w in vocab]}]})
        model_calls.append(request)
        return _completion(f"answer {len(model_calls)}")

    path = "/cached/openai/deployments/gpt-nofilter/chat/completions?api-version=2024-10-21"

    def ask(text: str) -> dict:
        return {"messages": [{"role": "system", "content": "Be brief."}, {"role": "user", "content": text}]}

    with _gateway("apim.foundry.json", handler) as client:
        first = client.post(path, headers=HEADERS, json=ask("What is the time off policy?"))
        second = client.post(path, headers=HEADERS, json=ask("What is the time-off policy?"))
        other_team = client.post(
            path, headers={"api-key": "ai-team-beta-key"}, json=ask("What is the time off policy?")
        )

    assert first.status_code == second.status_code == other_team.status_code == 200
    assert second.headers["x-apim-semantic-cache"] == "hit"
    assert second.json() == first.json()
    # vary-by on the subscription keeps one team's answers from another.
    assert "x-apim-semantic-cache" not in other_team.headers
    assert len(model_calls) == 2
    assert embedding_calls[0].url.path == "/openai/deployments/text-embedding-demo/embeddings"
    assert embedding_calls[0].headers["api-key"] == "local-foundry-key-alpha"
