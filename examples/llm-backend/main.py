from __future__ import annotations

import asyncio
import json
import math
import os
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from app.telemetry import configure_observability, instrument_fastapi_app, set_current_span_attributes

SERVICE_NAME = "llm-backend"
SERVICE_VERSION = "0.4.0"

telemetry = configure_observability(service_name=SERVICE_NAME, service_version=SERVICE_VERSION)

app = FastAPI(title="Mock LLM Backend", version=SERVICE_VERSION)

LATENCY_MS = int(os.getenv("MOCK_LLM_LATENCY_MS", "0"))


def _estimate_tokens(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    return max(1, math.ceil(len(stripped) / 4))


def _prompt_text(messages: list[Any]) -> str:
    chunks: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    chunks.append(part["text"])
                elif isinstance(part, str):
                    chunks.append(part)
    return "\n".join(chunks)


def _last_user_message(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return _prompt_text([message])
    return ""


def _completion_text(messages: list[Any], model: str, max_tokens: int | None) -> str:
    prompt = _last_user_message(messages).strip() or "(empty prompt)"
    text = (
        f"Simulated completion from '{model}'. You asked: \"{prompt}\". "
        "This deterministic reply exists so gateway policies can count tokens locally."
    )
    if max_tokens is not None and max_tokens > 0:
        # Roughly honour max_tokens with the same 4-chars-per-token heuristic.
        text = text[: max_tokens * 4]
    return text


def _responses_input_to_messages(input_value: Any) -> list[Any]:
    if isinstance(input_value, str):
        return [{"role": "user", "content": input_value}]
    if isinstance(input_value, list):
        messages: list[Any] = []
        for item in input_value:
            if isinstance(item, str):
                messages.append({"role": "user", "content": item})
            elif isinstance(item, dict):
                messages.append(item)
        return messages
    return []


def _anthropic_prompt_text(messages: list[Any], system: Any) -> str:
    parts: list[Any] = list(messages)
    if system is not None:
        parts.insert(0, {"role": "system", "content": system})
    return _prompt_text(parts)


def _chat_response_payload(*, model: str, messages: list[Any], max_tokens: int | None) -> dict[str, Any]:
    completion = _completion_text(messages, model, max_tokens)
    prompt_tokens = _estimate_tokens(_prompt_text(messages))
    completion_tokens = _estimate_tokens(completion)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": completion},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _sse_chunks(payload: dict[str, Any], *, include_usage: bool) -> list[str]:
    completion = payload["choices"][0]["message"]["content"]
    base = {
        "id": payload["id"],
        "object": "chat.completion.chunk",
        "created": payload["created"],
        "model": payload["model"],
    }
    thirds = max(1, len(completion) // 3)
    pieces = [completion[i : i + thirds] for i in range(0, len(completion), thirds)]
    chunks: list[dict[str, Any]] = [
        {**base, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}
    ]
    chunks.extend(
        {**base, "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]} for piece in pieces
    )
    chunks.append({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    if include_usage:
        chunks.append({**base, "choices": [], "usage": payload["usage"]})
    return [f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n" for chunk in chunks] + ["data: [DONE]\n\n"]


async def _chat_completion(request: Request, *, model_hint: str | None = None) -> JSONResponse | StreamingResponse:
    if LATENCY_MS > 0:
        await asyncio.sleep(LATENCY_MS / 1000)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "Request body must be JSON", "type": "invalid_request_error"}},
        )
    if not isinstance(body, dict) or not isinstance(body.get("messages"), list) or not body["messages"]:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "'messages' is required", "type": "invalid_request_error"}},
        )
    model = model_hint or str(body.get("model") or "mock-model")
    max_tokens = body.get("max_tokens") if isinstance(body.get("max_tokens"), int) else None
    payload = _chat_response_payload(model=model, messages=body["messages"], max_tokens=max_tokens)
    set_current_span_attributes(
        **{
            "llm.model": model,
            "llm.usage.total_tokens": payload["usage"]["total_tokens"],
        }
    )
    telemetry.logger.info(
        "chat completion served",
        extra={
            "event.name": "llm.chat_completion",
            "llm.model": model,
            "llm.usage.prompt_tokens": payload["usage"]["prompt_tokens"],
            "llm.usage.completion_tokens": payload["usage"]["completion_tokens"],
        },
    )
    if body.get("stream") is True:
        stream_options = body.get("stream_options")
        include_usage = isinstance(stream_options, dict) and stream_options.get("include_usage") is True
        return StreamingResponse(
            iter(_sse_chunks(payload, include_usage=include_usage)),
            media_type="text/event-stream",
        )
    return JSONResponse(content=payload)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME}


@app.post("/v1/chat/completions", response_model=None)
async def openai_chat_completions(request: Request) -> Response:
    return await _chat_completion(request)


@app.post("/openai/deployments/{deployment_id}/chat/completions", response_model=None)
async def azure_openai_chat_completions(deployment_id: str, request: Request) -> Response:
    return await _chat_completion(request, model_hint=deployment_id)


@app.post("/v1/responses", response_model=None)
async def openai_responses(request: Request) -> Response:
    if LATENCY_MS > 0:
        await asyncio.sleep(LATENCY_MS / 1000)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "Request body must be JSON", "type": "invalid_request_error"}},
        )
    if not isinstance(body, dict) or body.get("input") is None:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "'input' is required", "type": "invalid_request_error"}},
        )
    model = str(body.get("model") or "mock-model")
    messages = _responses_input_to_messages(body["input"])
    max_output_tokens_raw = body.get("max_output_tokens")
    max_output_tokens = max_output_tokens_raw if isinstance(max_output_tokens_raw, int) else None
    completion = _completion_text(messages, model, max_output_tokens)
    input_tokens = _estimate_tokens(_prompt_text(messages))
    output_tokens = _estimate_tokens(completion)
    payload = {
        "id": f"resp_{uuid.uuid4().hex[:24]}",
        "object": "response",
        "created_at": int(time.time()),
        "model": model,
        "status": "completed",
        "output": [
            {
                "type": "message",
                "id": f"msg_{uuid.uuid4().hex[:24]}",
                "role": "assistant",
                "content": [{"type": "output_text", "text": completion}],
            }
        ],
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    }
    set_current_span_attributes(
        **{
            "llm.model": model,
            "llm.usage.total_tokens": payload["usage"]["total_tokens"],
        }
    )
    telemetry.logger.info(
        "responses completion served",
        extra={
            "event.name": "llm.responses",
            "llm.model": model,
            "llm.usage.input_tokens": input_tokens,
            "llm.usage.output_tokens": output_tokens,
        },
    )
    return JSONResponse(content=payload)


@app.post("/v1/messages", response_model=None)
async def anthropic_messages(request: Request) -> Response:
    if LATENCY_MS > 0:
        await asyncio.sleep(LATENCY_MS / 1000)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse(
            status_code=400,
            content={
                "type": "error",
                "error": {"type": "invalid_request_error", "message": "Request body must be JSON"},
            },
        )
    if not isinstance(body, dict) or not isinstance(body.get("messages"), list) or not body["messages"]:
        return JSONResponse(
            status_code=400,
            content={
                "type": "error",
                "error": {"type": "invalid_request_error", "message": "'messages' is required"},
            },
        )
    max_tokens = body.get("max_tokens")
    if not isinstance(max_tokens, int):
        return JSONResponse(
            status_code=400,
            content={
                "type": "error",
                "error": {"type": "invalid_request_error", "message": "'max_tokens' is required"},
            },
        )
    model = str(body.get("model") or "mock-model")
    messages = body["messages"]
    system = body.get("system")
    completion = _completion_text(messages, model, max_tokens)
    input_tokens = _estimate_tokens(_anthropic_prompt_text(messages, system))
    output_tokens = _estimate_tokens(completion)
    payload = {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": completion}],
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    }
    set_current_span_attributes(
        **{
            "llm.model": model,
            "llm.usage.total_tokens": input_tokens + output_tokens,
        }
    )
    telemetry.logger.info(
        "messages completion served",
        extra={
            "event.name": "llm.messages",
            "llm.model": model,
            "llm.usage.input_tokens": input_tokens,
            "llm.usage.output_tokens": output_tokens,
        },
    )
    return JSONResponse(content=payload)


instrument_fastapi_app(app, telemetry)
