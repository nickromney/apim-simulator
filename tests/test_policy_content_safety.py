"""llm-content-safety as a client of an Azure AI Content Safety backend.

https://learn.microsoft.com/en-us/azure/api-management/llm-content-safety-policy
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import GatewayConfig
from app.main import create_app
from app.policy import parse_policies_xml
from app.policy_content_safety import completion_text, prompt_text

PROMPT = {"model": "m", "messages": [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "hi"}]}


def _gateway(policy: str, handler) -> TestClient:
    config = GatewayConfig.model_validate(
        {
            "allow_anonymous": True,
            "proxy_streaming": False,
            "backends": {"safety": {"url": "http://safety.test", "header_credentials": {"api-key": "demo-key"}}},
            "apis": {
                "chat": {
                    "name": "chat",
                    "path": "chat",
                    "upstream_base_url": "http://model.test",
                    "policies_xml": f"<policies><inbound>{policy}</inbound><backend><forward-request /></backend>"
                    "<outbound /><on-error /></policies>",
                    "operations": {"c": {"name": "c", "method": "POST", "url_template": "/c"}},
                }
            },
        }
    )
    return TestClient(create_app(config=config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))))


@pytest.mark.contract("POLICY-LLM-CONTENT-SAFETY")
def test_blocklist_match_blocks_and_backend_credentials_are_sent() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/contentsafety/text:analyze":
            return httpx.Response(200, json={"blocklistsMatch": [{"blocklistName": "words"}], "categoriesAnalysis": []})
        return httpx.Response(200, json={"choices": []})

    policy = '<llm-content-safety backend-id="safety"><blocklists><id>words</id></blocklists></llm-content-safety>'
    with _gateway(policy, handler) as client:
        response = client.post("/chat/c", json=PROMPT)

    assert response.status_code == 403
    analyze = seen[0]
    assert analyze.headers["api-key"] == "demo-key"
    assert analyze.url.params["api-version"] == "2024-09-01"
    assert json.loads(analyze.content) == {
        "text": "Be brief.\nhi",
        "outputType": "FourSeverityLevels",
        "blocklistNames": ["words"],
    }


@pytest.mark.contract("POLICY-LLM-CONTENT-SAFETY")
def test_below_threshold_passes_and_service_errors_enter_on_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/contentsafety/text:analyze":
            text = json.loads(request.content)["text"]
            if "fail" in text:
                return httpx.Response(500)
            return httpx.Response(200, json={"categoriesAnalysis": [{"category": "Hate", "severity": 2}]})
        return httpx.Response(200, json={"choices": []})

    policy = (
        '<llm-content-safety backend-id="safety"><categories output-type="EightSeverityLevels">'
        '<category name="Hate" threshold="3" /></categories></llm-content-safety>'
    )
    with _gateway(policy, handler) as client:
        allowed = client.post("/chat/c", json=PROMPT)
        failed = client.post("/chat/c", json={"prompt": "fail"})

    assert allowed.status_code == 200
    assert failed.status_code == 500


@pytest.mark.contract("POLICY-LLM-CONTENT-SAFETY")
def test_unknown_backend_and_enforce_on_completions_are_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"categoriesAnalysis": []})

    with _gateway('<llm-content-safety backend-id="missing" />', handler) as client:
        assert client.post("/chat/c", json=PROMPT).status_code == 500
    enforce = '<llm-content-safety backend-id="safety" enforce-on-completions="true" />'
    with _gateway(enforce, handler) as client:
        assert client.post("/chat/c", json=PROMPT).status_code == 500


@pytest.mark.contract("POLICY-LLM-CONTENT-SAFETY")
@pytest.mark.parametrize(
    "xml",
    [
        "<llm-content-safety />",
        '<llm-content-safety backend-id="b" colour="red" />',
        '<llm-content-safety backend-id="b"><categories><category name="Hate" /></categories></llm-content-safety>',
        '<llm-content-safety backend-id="b"><rules /></llm-content-safety>',
    ],
)
def test_malformed_policy_is_rejected(xml: str) -> None:
    with pytest.raises(Exception, match="llm-content-safety"):
        parse_policies_xml(f"<policies><inbound>{xml}</inbound><backend /><outbound /><on-error /></policies>")


@pytest.mark.contract("POLICY-LLM-CONTENT-SAFETY")
def test_policy_is_not_allowed_in_backend_section() -> None:
    xml = '<policies><inbound /><backend><llm-content-safety backend-id="b" /></backend><outbound /><on-error /></policies>'
    with pytest.raises(Exception, match="llm-content-safety"):
        parse_policies_xml(xml)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"messages": [{"role": "user", "content": [{"type": "text", "text": "part"}]}]}, "part"),
        ({"system": "rules", "messages": [{"role": "user", "content": "ask"}]}, "rules\nask"),
        ({"input": "responses api"}, "responses api"),
        ({"prompt": ["one", "two"]}, "one\ntwo"),
    ],
)
def test_prompt_text_reads_each_api_shape(body: dict, expected: str) -> None:
    assert prompt_text(json.dumps(body).encode()) == expected


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"choices": [{"message": {"content": "chat"}}]}, "chat"),
        ({"choices": [{"text": "legacy"}]}, "legacy"),
        ({"output_text": "responses"}, "responses"),
        ({"content": [{"type": "text", "text": "anthropic"}]}, "anthropic"),
    ],
)
def test_completion_text_reads_each_api_shape(body: dict, expected: str) -> None:
    assert completion_text(json.dumps(body).encode()) == expected
    assert prompt_text(b"plain text") == "plain text"
