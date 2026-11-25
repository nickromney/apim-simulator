"""llm-semantic-cache-lookup/-store as a client of an embeddings backend.

https://learn.microsoft.com/en-us/azure/api-management/llm-semantic-cache-lookup-policy
https://learn.microsoft.com/en-us/azure/api-management/llm-semantic-cache-store-policy
"""

from __future__ import annotations

import json
import re

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import GatewayConfig
from app.main import create_app
from app.policy import parse_policies_xml

VOCAB = ("time", "off", "policy", "holiday", "capital", "france", "spain", "brief", "be", "what", "is", "the")
LOOKUP = (
    '<llm-semantic-cache-lookup score-threshold="0.2" embeddings-backend-id="embeddings" '
    'embeddings-backend-auth="system-assigned" ignore-system-messages="true" max-message-count="2" />'
)
STORE = '<llm-semantic-cache-store duration="120" />'


def _vector(text: str) -> list[float]:
    words = re.findall(r"[a-z]+", text.lower())
    return [float(words.count(term)) for term in VOCAB]


def _chat(*user: str, system: str | None = None) -> dict:
    messages = [{"role": "system", "content": system}] if system else []
    return {"messages": messages + [{"role": "user", "content": text} for text in user]}


class _Backends:
    def __init__(self) -> None:
        self.embeddings: list[httpx.Request] = []
        self.model_calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "embeddings.test":
            self.embeddings.append(request)
            text = json.loads(request.content)["input"]
            return httpx.Response(200, json={"data": [{"embedding": _vector(text), "index": 0}]})
        self.model_calls += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": f"answer {self.model_calls}"}}]})


def _gateway(backends: _Backends, *, lookup: str = LOOKUP, embeddings_backend: dict | None = None) -> TestClient:
    config = GatewayConfig.model_validate(
        {
            "allow_anonymous": True,
            "workload_identity": {"mode": "demo"},
            "backends": {
                "embeddings": embeddings_backend
                or {"url": "http://embeddings.test/openai/deployments/text-embedding-demo/embeddings"}
            },
            "apis": {
                "chat": {
                    "name": "chat",
                    "path": "chat",
                    "upstream_base_url": "http://model.test",
                    "policies_xml": f"<policies><inbound>{lookup}</inbound><backend><forward-request /></backend>"
                    f"<outbound>{STORE}</outbound><on-error /></policies>",
                    "operations": {"c": {"name": "c", "method": "POST", "url_template": "/c"}},
                }
            },
        }
    )
    return TestClient(create_app(config=config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(backends))))


@pytest.mark.contract("POLICY-LLM-SEMANTIC-CACHE")
def test_reworded_prompt_hits_and_skips_the_model() -> None:
    backends = _Backends()
    with _gateway(backends) as client:
        first = client.post("/chat/c", json=_chat("What is the time off policy?", system="Be brief."))
        second = client.post("/chat/c", json=_chat("what is the holiday time off policy", system="Other."))
        other = client.post("/chat/c", json=_chat("What is the capital of France?"))

    assert first.status_code == second.status_code == other.status_code == 200
    assert "x-apim-semantic-cache" not in first.headers
    assert second.headers["x-apim-semantic-cache"] == "hit"
    assert float(second.headers["x-apim-semantic-cache-score"]) <= 0.2
    assert second.json() == first.json()
    assert other.json()["choices"][0]["message"]["content"] == "answer 2"
    assert backends.model_calls == 2
    # ignore-system-messages keeps the system prompt out of the vector.
    assert "Be brief" not in json.loads(backends.embeddings[0].content)["input"]
    request = backends.embeddings[0]
    assert request.url.params["api-version"] == "2024-10-21"
    assert request.headers["authorization"].startswith("Bearer ")


@pytest.mark.contract("POLICY-LLM-SEMANTIC-CACHE")
def test_max_message_count_and_streaming_skip_the_cache() -> None:
    backends = _Backends()
    with _gateway(backends) as client:
        long_dialog = _chat("time off", "policy", "holiday")
        client.post("/chat/c", json=long_dialog)
        client.post("/chat/c", json=long_dialog)
        streamed = {**_chat("time off policy"), "stream": True}
        client.post("/chat/c", json=streamed)
        client.post("/chat/c", json=streamed)

    assert backends.model_calls == 4
    assert backends.embeddings == []


@pytest.mark.contract("POLICY-LLM-SEMANTIC-CACHE")
def test_vary_by_partitions_and_backend_key_replaces_managed_identity() -> None:
    backends = _Backends()
    lookup = LOOKUP.replace(
        " />",
        '><vary-by>@(context.Request.Headers.GetValueOrDefault("x-team",""))</vary-by></llm-semantic-cache-lookup>',
    )
    keyed = {
        "url": "http://embeddings.test/openai/deployments/text-embedding-demo/embeddings",
        "header_credentials": {"api-key": "embeddings-key"},
    }
    with _gateway(backends, lookup=lookup, embeddings_backend=keyed) as client:
        client.post("/chat/c", json=_chat("time off policy"), headers={"x-team": "a"})
        b = client.post("/chat/c", json=_chat("time off policy"), headers={"x-team": "b"})
        a = client.post("/chat/c", json=_chat("time off policy"), headers={"x-team": "a"})

    assert "x-apim-semantic-cache" not in b.headers
    assert a.headers["x-apim-semantic-cache"] == "hit"
    assert backends.model_calls == 2
    assert backends.embeddings[0].headers["api-key"] == "embeddings-key"
    assert "authorization" not in backends.embeddings[0].headers


@pytest.mark.contract("POLICY-LLM-SEMANTIC-CACHE")
def test_azure_openai_aliases_parse_and_invalid_attributes_are_rejected() -> None:
    aliases = LOOKUP.replace("llm-", "azure-openai-")
    docs = parse_policies_xml(
        f"<policies><inbound>{aliases}</inbound><backend /><outbound>"
        '<azure-openai-semantic-cache-store duration="60" /></outbound><on-error /></policies>'
    )
    assert docs is not None
    for bad in (
        LOOKUP.replace('embeddings-backend-auth="system-assigned"', 'embeddings-backend-auth="user-assigned"'),
        LOOKUP.replace('score-threshold="0.2" ', ""),
        LOOKUP.replace(" />", ' cache-ttl="3" />'),
    ):
        with pytest.raises(HTTPException):
            parse_policies_xml(f"<policies><inbound>{bad}</inbound><backend /><outbound /><on-error /></policies>")
