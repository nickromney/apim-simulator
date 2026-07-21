from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import HTTPException

from app.policy import (
    LlmEmitTokenMetric,
    LlmTokenLimit,
    PolicyRequest,
    PolicyRuntime,
    apply_inbound,
    finalize_deferred_actions,
    parse_policies_xml,
)

PROMPT_BODY = json.dumps(
    {
        "model": "gpt-demo",
        "messages": [
            {"role": "system", "content": "You are a terse assistant."},
            {"role": "user", "content": "Summarise the plot of Hamlet in one sentence."},
        ],
    }
).encode("utf-8")

USAGE_RESPONSE = json.dumps(
    {
        "id": "chatcmpl-demo",
        "choices": [{"message": {"role": "assistant", "content": "Everyone dies."}}],
        "usage": {"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60},
    }
).encode("utf-8")


def _policy_doc(xml: str):
    return parse_policies_xml(xml)


def _request(store: dict[str, Any], quota_store: dict[str, Any], *, body: bytes = PROMPT_BODY) -> PolicyRequest:
    return PolicyRequest(
        method="POST",
        path="/openai/deployments/gpt-demo/chat/completions",
        query={},
        headers={"content-type": "application/json"},
        variables={
            "rate_limit_store": store,
            "quota_store": quota_store,
            "subscription_id": "sub-ai-demo",
            "api_id": "llm-api",
            "operation_id": "chat",
            "client_ip": "127.0.0.1",
        },
        body=body,
    )


def _finalize(
    req: PolicyRequest,
    runtime: PolicyRuntime,
    *,
    status: int = 200,
    body: bytes = USAGE_RESPONSE,
    media_type: str | None = "application/json",
) -> dict[str, str]:
    response_headers: dict[str, str] = {}
    final_req = PolicyRequest(
        method=req.method,
        path=req.path,
        query=dict(req.query),
        headers=dict(req.headers),
        variables=req.variables,
        body=req.body,
        response_status_code=status,
        response_headers=response_headers,
        response_body=body,
        response_media_type=media_type,
    )
    finalize_deferred_actions(final_req, runtime)
    return response_headers


@pytest.mark.contract("POLICY-LLM-TOKEN-LIMIT")
def test_llm_token_limit_consumes_actual_usage_then_blocks() -> None:
    doc = _policy_doc(
        """\
<policies>
  <inbound>
    <llm-token-limit counter-key="@(context.Subscription.Id)"
        tokens-per-minute="100"
        estimate-prompt-tokens="false"
        remaining-tokens-header-name="x-remaining-tokens"
        tokens-consumed-header-name="x-tokens-consumed" />
  </inbound>
  <backend />
  <outbound />
  <on-error />
</policies>
"""
    )
    store: dict[str, Any] = {}
    quota_store: dict[str, Any] = {}

    runtime = PolicyRuntime()
    req = _request(store, quota_store)
    assert apply_inbound([doc], req, runtime) is None
    assert req.variables["_policy_response_buffering_required"] is True
    headers = _finalize(req, runtime)
    assert headers["x-tokens-consumed"] == "60"
    assert headers["x-remaining-tokens"] == "40"

    runtime = PolicyRuntime()
    req = _request(store, quota_store)
    assert apply_inbound([doc], req, runtime) is None
    _finalize(req, runtime)

    # 120 tokens consumed in the window, above the 100 limit.
    runtime = PolicyRuntime()
    req = _request(store, quota_store)
    blocked = apply_inbound([doc], req, runtime)
    assert blocked is not None
    assert blocked.status_code == 429
    assert "retry-after" in blocked.headers
    assert b"Token limit is exceeded" in blocked.body


@pytest.mark.contract("POLICY-LLM-TOKEN-LIMIT")
def test_llm_token_limit_estimate_blocks_before_backend() -> None:
    doc = _policy_doc(
        """\
<policies>
  <inbound>
    <llm-token-limit counter-key="demo" tokens-per-minute="10" estimate-prompt-tokens="true" />
  </inbound>
  <backend />
  <outbound />
  <on-error />
</policies>
"""
    )
    runtime = PolicyRuntime()
    req = _request({}, {})
    blocked = apply_inbound([doc], req, runtime)
    assert blocked is not None
    assert blocked.status_code == 429
    assert not runtime.deferred_actions


@pytest.mark.contract("POLICY-LLM-TOKEN-LIMIT")
def test_llm_token_limit_quota_returns_403() -> None:
    doc = _policy_doc(
        """\
<policies>
  <inbound>
    <llm-token-limit counter-key="demo"
        token-quota="50" token-quota-period="Hourly"
        estimate-prompt-tokens="false"
        remaining-quota-tokens-header-name="x-remaining-quota" />
  </inbound>
  <backend />
  <outbound />
  <on-error />
</policies>
"""
    )
    store: dict[str, Any] = {}
    quota_store: dict[str, Any] = {}

    runtime = PolicyRuntime()
    req = _request(store, quota_store)
    assert apply_inbound([doc], req, runtime) is None
    headers = _finalize(req, runtime)
    assert headers["x-remaining-quota"] == "0"

    runtime = PolicyRuntime()
    req = _request(store, quota_store)
    blocked = apply_inbound([doc], req, runtime)
    assert blocked is not None
    assert blocked.status_code == 403
    assert b"Token quota is exceeded" in blocked.body
    assert int(blocked.headers["retry-after"]) <= 3600


@pytest.mark.contract("POLICY-LLM-TOKEN-LIMIT")
def test_llm_token_limit_streaming_falls_back_to_estimate() -> None:
    doc = _policy_doc(
        """\
<policies>
  <inbound>
    <llm-token-limit counter-key="demo" tokens-per-minute="1000"
        estimate-prompt-tokens="true"
        tokens-consumed-header-name="x-tokens-consumed" />
  </inbound>
  <backend />
  <outbound />
  <on-error />
</policies>
"""
    )
    runtime = PolicyRuntime()
    req = _request({}, {})
    assert apply_inbound([doc], req, runtime) is None
    headers = _finalize(req, runtime, body=b"data: {}\n\ndata: [DONE]\n\n", media_type="text/event-stream")
    consumed = int(headers["x-tokens-consumed"])
    assert consumed > 0


@pytest.mark.contract("POLICY-LLM-TOKEN-LIMIT")
def test_llm_token_limit_reads_usage_from_sse_chunk() -> None:
    doc = _policy_doc(
        """\
<policies>
  <inbound>
    <llm-token-limit counter-key="demo" tokens-per-minute="1000"
        estimate-prompt-tokens="false"
        tokens-consumed-header-name="x-tokens-consumed" />
  </inbound>
  <backend />
  <outbound />
  <on-error />
</policies>
"""
    )
    runtime = PolicyRuntime()
    req = _request({}, {})
    assert apply_inbound([doc], req, runtime) is None
    sse_body = (
        b'data: {"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
        b'data: {"choices":[{"index":0,"delta":{"content":"Hello there"},"finish_reason":null}]}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":30,"completion_tokens":12,"total_tokens":42}}\n\n'
        b"data: [DONE]\n\n"
    )
    headers = _finalize(req, runtime, body=sse_body, media_type="text/event-stream")
    assert headers["x-tokens-consumed"] == "42"


@pytest.mark.contract("POLICY-LLM-TOKEN-LIMIT")
def test_llm_token_limit_estimates_completion_from_sse_deltas() -> None:
    doc = _policy_doc(
        """\
<policies>
  <inbound>
    <llm-token-limit counter-key="demo" tokens-per-minute="1000"
        estimate-prompt-tokens="true"
        tokens-consumed-header-name="x-tokens-consumed" />
  </inbound>
  <backend />
  <outbound />
  <on-error />
</policies>
"""
    )
    runtime = PolicyRuntime()
    req = _request({}, {})
    assert apply_inbound([doc], req, runtime) is None
    completion_text = "This streamed completion has no usage chunk at all, only content deltas."
    half = len(completion_text) // 2
    sse_body = (
        b'data: {"choices":[{"index":0,"delta":{"content":"'
        + completion_text[:half].encode("utf-8")
        + b'"},"finish_reason":null}]}\n\n'
        + b'data: {"choices":[{"index":0,"delta":{"content":"'
        + completion_text[half:].encode("utf-8")
        + b'"},"finish_reason":null}]}\n\n'
        + b"data: [DONE]\n\n"
    )
    prompt_only = _finalize_consumed(req, runtime, body=b"not json", media_type="text/plain")
    runtime = PolicyRuntime()
    req = _request({}, {})
    assert apply_inbound([doc], req, runtime) is None
    with_deltas = _finalize_consumed(req, runtime, body=sse_body, media_type="text/event-stream")
    # Delta text adds completion tokens on top of the prompt estimate.
    assert with_deltas > prompt_only


def _finalize_consumed(req: PolicyRequest, runtime: PolicyRuntime, *, body: bytes, media_type: str) -> int:
    headers = _finalize(req, runtime, body=body, media_type=media_type)
    return int(headers["x-tokens-consumed"])


@pytest.mark.contract("POLICY-LLM-TOKEN-LIMIT")
def test_llm_token_limit_does_not_count_error_responses() -> None:
    doc = _policy_doc(
        """\
<policies>
  <inbound>
    <llm-token-limit counter-key="demo" tokens-per-minute="1000"
        estimate-prompt-tokens="true"
        tokens-consumed-header-name="x-tokens-consumed" />
  </inbound>
  <backend />
  <outbound />
  <on-error />
</policies>
"""
    )
    runtime = PolicyRuntime()
    req = _request({}, {})
    assert apply_inbound([doc], req, runtime) is None
    headers = _finalize(req, runtime, status=502, body=b"Bad Gateway", media_type="text/plain")
    assert headers["x-tokens-consumed"] == "0"


@pytest.mark.contract("POLICY-LLM-TOKEN-LIMIT")
def test_azure_openai_token_limit_alias_parses() -> None:
    doc = _policy_doc(
        """\
<policies>
  <inbound>
    <azure-openai-token-limit counter-key="demo" tokens-per-minute="100" estimate-prompt-tokens="false" />
  </inbound>
  <backend />
  <outbound />
  <on-error />
</policies>
"""
    )
    assert isinstance(doc.inbound[0], LlmTokenLimit)


@pytest.mark.contract("POLICY-LLM-TOKEN-LIMIT")
@pytest.mark.parametrize(
    "xml",
    [
        '<llm-token-limit tokens-per-minute="100" estimate-prompt-tokens="false" />',
        '<llm-token-limit counter-key="demo" tokens-per-minute="100" />',
        '<llm-token-limit counter-key="demo" estimate-prompt-tokens="false" />',
        '<llm-token-limit counter-key="demo" token-quota="100" estimate-prompt-tokens="false" />',
    ],
)
def test_llm_token_limit_rejects_invalid_configuration(xml: str) -> None:
    with pytest.raises(HTTPException):
        _policy_doc(f"<policies><inbound>{xml}</inbound><backend /><outbound /><on-error /></policies>")


@pytest.mark.contract("POLICY-LLM-EMIT-TOKEN-METRIC")
def test_llm_emit_token_metric_emits_usage_with_dimensions() -> None:
    doc = _policy_doc(
        """\
<policies>
  <inbound>
    <llm-emit-token-metric namespace="ai-usage">
      <dimension name="Subscription ID" />
      <dimension name="deployment" value="gpt-demo" />
    </llm-emit-token-metric>
  </inbound>
  <backend />
  <outbound />
  <on-error />
</policies>
"""
    )
    assert isinstance(doc.inbound[0], LlmEmitTokenMetric)
    emitted: list[tuple[int, dict[str, str]]] = []
    runtime = PolicyRuntime(llm_metric_emitter=lambda amount, attributes: emitted.append((amount, attributes)))
    req = _request({}, {})
    assert apply_inbound([doc], req, runtime) is None
    assert req.variables["_policy_response_buffering_required"] is True
    _finalize(req, runtime)

    amounts = {attributes["apim.llm.token.type"]: amount for amount, attributes in emitted}
    assert amounts == {"prompt": 40, "completion": 20, "total": 60}
    for _, attributes in emitted:
        assert attributes["apim.llm.metric.namespace"] == "ai-usage"
        assert attributes["apim.llm.dimension.Subscription ID"] == "sub-ai-demo"
        assert attributes["apim.llm.dimension.deployment"] == "gpt-demo"


@pytest.mark.contract("POLICY-LLM-EMIT-TOKEN-METRIC")
def test_llm_emit_token_metric_records_trace_step() -> None:
    from app.policy import PolicyTraceCollector

    doc = _policy_doc(
        """\
<policies>
  <inbound>
    <azure-openai-emit-token-metric>
      <dimension name="API ID" />
    </azure-openai-emit-token-metric>
  </inbound>
  <backend />
  <outbound />
  <on-error />
</policies>
"""
    )
    runtime = PolicyRuntime(trace=PolicyTraceCollector())
    req = _request({}, {})
    assert apply_inbound([doc], req, runtime) is None
    _finalize(req, runtime)
    steps = [step for step in runtime.trace.steps if step["step"] == "llm-emit-token-metric"]
    assert steps
    final_step = steps[-1]
    assert final_step["total_tokens"] == 60
    assert final_step["dimensions"] == {"API ID": "llm-api"}
