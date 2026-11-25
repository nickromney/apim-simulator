"""llm-semantic-cache-lookup and llm-semantic-cache-store as an embeddings client.

The lookup sends the prompt to an embeddings backend, compares the vector with
cached prompt vectors by cosine distance, and returns the stored completion
when the closest one is within ``score-threshold``. A miss lets the request
reach the model; the store policy then saves the prompt vector and the
completion for ``duration`` seconds. Vectors come from the backend; the
sibling aifoundry-simulator serves a local embeddings deployment.

Adapted: Azure keeps the vectors in an external cache (Azure Managed Redis
with RediSearch). The simulator keeps them in gateway memory, so a restart or
a configuration reload empties the cache.

https://learn.microsoft.com/en-us/azure/api-management/llm-semantic-cache-lookup-policy
https://learn.microsoft.com/en-us/azure/api-management/llm-semantic-cache-store-policy
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any
from xml.etree.ElementTree import Element

import httpx
from fastapi import HTTPException

from app.policy import (
    PolicyNode,
    PolicyRequest,
    PolicyRuntime,
    ResponseSpec,
    _policy_bool,
    _policy_float,
    _policy_int,
    _policy_now,
    _record_step,
    _reject_unknown_attributes,
    issue_local_managed_identity_token,
    render_policy_value,
)

LOOKUP_TAGS = {"llm-semantic-cache-lookup", "azure-openai-semantic-cache-lookup"}
STORE_TAGS = {"llm-semantic-cache-store", "azure-openai-semantic-cache-store"}
EMBEDDINGS_API_VERSION = "2024-10-21"
# The token audience Azure OpenAI and AI Foundry accept for managed identity.
COGNITIVE_SERVICES_RESOURCE = "https://cognitiveservices.azure.com"
CACHE_KEY_PREFIX = "llm-semantic-cache:"
CONTEXT_VARIABLE = "_policy_semantic_cache_context"
# Responses served from the cache carry headers that name the policy result,
# so a reader can tell a hit from a model call without a trace.
HIT_HEADER = "x-apim-semantic-cache"


@dataclass(frozen=True)
class SemanticCacheEntry:
    expires_at: float
    vector: tuple[float, ...]
    status_code: int
    headers: dict[str, str]
    body: bytes
    media_type: str | None


@dataclass(frozen=True)
class SemanticCacheContext:
    partition: str
    vector: tuple[float, ...]


@dataclass(frozen=True)
class LlmSemanticCacheLookup(PolicyNode):
    score_threshold: str
    embeddings_backend_id: str
    embeddings_backend_auth: str
    ignore_system_messages: str | None = None
    max_message_count: str | None = None
    vary_by: tuple[str, ...] = field(default_factory=tuple)

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        raise RuntimeError("llm-semantic-cache-lookup must be executed through apply_async")

    async def apply_async(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        if runtime is None or runtime.http_client is None or runtime.gateway_config is None:
            raise HTTPException(status_code=500, detail="llm-semantic-cache-lookup requires a gateway runtime")
        threshold = _policy_float(self.score_threshold, req, runtime)
        if not 0.0 <= threshold <= 1.0:
            raise HTTPException(status_code=500, detail="llm-semantic-cache-lookup score-threshold must be 0.0 to 1.0")
        text, skip = self._prompt(req, runtime)
        if skip is not None:
            return _skip(runtime, skip[0], **skip[1])

        backend_id = render_policy_value(self.embeddings_backend_id, req, runtime)
        vector = await _embed(text, backend_id, req, runtime)
        partition = _partition(req, runtime, self.vary_by)
        # The context is set before the hit check so the store policy has a
        # vector to save on a miss.
        req.variables[CONTEXT_VARIABLE] = SemanticCacheContext(partition=partition, vector=vector)
        req.variables["_policy_response_buffering_required"] = True
        best = _closest(runtime, partition, vector)
        if best is not None and best[0] <= threshold:
            distance, entry = best
            _record_step(
                runtime,
                "llm-semantic-cache-lookup",
                {"status": "hit", "score": round(distance, 6), "score_threshold": threshold, "adapted": True},
            )
            headers = {**entry.headers, HIT_HEADER: "hit", f"{HIT_HEADER}-score": f"{distance:.6f}"}
            return ResponseSpec(
                status_code=entry.status_code, headers=headers, body=entry.body, media_type=entry.media_type
            )
        detail: dict[str, Any] = {"status": "miss", "score_threshold": threshold, "adapted": True}
        if best is not None:
            detail["closest_score"] = round(best[0], 6)
        _record_step(runtime, "llm-semantic-cache-lookup", detail)
        return None

    def _prompt(self, req: PolicyRequest, runtime: PolicyRuntime) -> tuple[str, tuple[str, dict[str, Any]] | None]:
        """The text to embed, or why this request skips the cache."""
        payload = _json(req.body)
        if not isinstance(payload, dict):
            return "", ("no-json-prompt", {})
        if payload.get("stream") is True:
            # Not simulated: replaying a cached completion as an SSE stream.
            return "", ("streaming", {})
        messages = _dialog(payload, ignore_system=_policy_bool(self.ignore_system_messages, req, runtime))
        if self.max_message_count is not None:
            limit = _policy_int(self.max_message_count, req, runtime, default=0)
            if len(messages) > limit:
                return "", ("max-message-count", {"messages": len(messages), "max_message_count": limit})
        text = "\n".join(messages) if messages else _prompt_fields(payload)
        if not text:
            return "", ("no-prompt-text", {})
        return text, None


@dataclass(frozen=True)
class LlmSemanticCacheStore(PolicyNode):
    duration: str

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        if runtime is None:
            return None
        context = req.variables.get(CONTEXT_VARIABLE)
        if not isinstance(context, SemanticCacheContext):
            return None
        if req.response_status_code != 200:
            _record_step(runtime, "llm-semantic-cache-store", {"status": "skipped", "reason": "not-200"})
            return None
        if not req.response_body:
            _record_step(runtime, "llm-semantic-cache-store", {"status": "skipped", "reason": "no-body"})
            return None
        ttl = _policy_int(self.duration, req, runtime, default=0)
        if ttl <= 0:
            _record_step(runtime, "llm-semantic-cache-store", {"status": "skipped", "reason": "non-positive-duration"})
            return None
        now = _policy_now(runtime)
        key = CACHE_KEY_PREFIX + context.partition
        live = [entry for entry in runtime.response_cache.get(key, []) if entry.expires_at > now]
        headers = {
            name: value for name, value in (req.response_headers or {}).items() if name.lower() != "content-length"
        }
        live.append(
            SemanticCacheEntry(
                expires_at=now + ttl,
                vector=context.vector,
                status_code=200,
                headers=headers,
                body=req.response_body,
                media_type=req.response_media_type,
            )
        )
        runtime.response_cache[key] = live
        _record_step(runtime, "llm-semantic-cache-store", {"status": "stored", "duration_seconds": ttl})
        return None


def _skip(runtime: PolicyRuntime, reason: str, **detail: Any) -> None:
    _record_step(runtime, "llm-semantic-cache-lookup", {"status": "skipped", "reason": reason, **detail})
    return None


def _json(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part["text"] for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def _dialog(payload: dict[str, Any], *, ignore_system: bool) -> list[str]:
    """Chat message texts, without system and developer messages when asked."""
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return []
    texts: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        if ignore_system and message.get("role") in {"system", "developer"}:
            continue
        text = _content_text(message.get("content"))
        if text:
            texts.append(f"{message.get('role', 'user')}: {text}")
    return texts


def _prompt_fields(payload: dict[str, Any]) -> str:
    """The prompt of a completions or Responses request, which has no messages."""
    for key in ("prompt", "input"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "\n".join(item for item in value if isinstance(item, str))
    return ""


def _partition(req: PolicyRequest, runtime: PolicyRuntime, vary_by: tuple[str, ...]) -> str:
    """The API plus each vary-by value; entries never match across partitions.

    Adapted: Learn documents vary-by as the partition but not whether the
    cache is shared between APIs. Keeping each API separate stops one API's
    completions answering another's prompts.
    """
    api_id = str(req.variables.get("api_id") or "")
    values = [render_policy_value(item, req, runtime) for item in vary_by]
    return json.dumps([api_id, *values], separators=(",", ":"))


def _cosine_distance(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        return 1.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    if norm == 0:
        return 1.0
    return max(0.0, min(1.0, 1.0 - dot / norm))


def _closest(
    runtime: PolicyRuntime, partition: str, vector: tuple[float, ...]
) -> tuple[float, SemanticCacheEntry] | None:
    now = _policy_now(runtime)
    key = CACHE_KEY_PREFIX + partition
    live = [entry for entry in runtime.response_cache.get(key, []) if entry.expires_at > now]
    if live:
        runtime.response_cache[key] = live
    else:
        runtime.response_cache.pop(key, None)
        return None
    return min(((_cosine_distance(vector, entry.vector), entry) for entry in live), key=lambda item: item[0])


async def _embed(text: str, backend_id: str, req: PolicyRequest, runtime: PolicyRuntime) -> tuple[float, ...]:
    """Call the embeddings backend, whose URL is the embeddings deployment endpoint."""
    from app.backend_pool import apply_backend_credentials

    config = runtime.gateway_config
    backend = config.backends.get(backend_id) if config else None
    if backend is None or not backend.url:
        raise HTTPException(status_code=500, detail=f"llm-semantic-cache-lookup unknown backend: {backend_id}")
    callout = PolicyRequest(method="POST", path="/", query={}, headers={}, variables=dict(req.variables))
    auth = apply_backend_credentials(backend, callout, config)
    headers = dict(callout.headers.as_header_pairs())
    if not any(name.lower() in {"authorization", "api-key"} for name in headers):
        # embeddings-backend-auth="system-assigned": the gateway's managed
        # identity, which the keyless Foundry pairing verifies.
        token = issue_local_managed_identity_token(COGNITIVE_SERVICES_RESOURCE, config=config)
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = await runtime.http_client.post(
            backend.url,
            params={"api-version": EMBEDDINGS_API_VERSION, **callout.query},
            headers=headers,
            json={"input": text},
            auth=auth or httpx.USE_CLIENT_DEFAULT,
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=500, detail=f"llm-semantic-cache-lookup embeddings call failed: {exc}") from exc
    if response.status_code != 200:
        raise HTTPException(
            status_code=500, detail=f"llm-semantic-cache-lookup embeddings returned {response.status_code}"
        )
    try:
        vector = response.json()["data"][0]["embedding"]
        return tuple(float(value) for value in vector)
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(
            status_code=500, detail="llm-semantic-cache-lookup embeddings response has no vector"
        ) from exc


def _parse_lookup(element: Element) -> LlmSemanticCacheLookup:
    _reject_unknown_attributes(
        element,
        {
            "id",
            "score-threshold",
            "embeddings-backend-id",
            "embeddings-backend-auth",
            "ignore-system-messages",
            "max-message-count",
        },
        element.tag,
    )
    values = {}
    for name in ("score-threshold", "embeddings-backend-id", "embeddings-backend-auth"):
        value = (element.attrib.get(name) or "").strip()
        if not value:
            raise ValueError(f"{element.tag} requires {name}")
        values[name] = value
    if values["embeddings-backend-auth"] != "system-assigned":
        raise ValueError(f"{element.tag} embeddings-backend-auth must be system-assigned")
    unknown = {child.tag for child in element} - {"vary-by"}
    if unknown:
        raise ValueError(f"{element.tag} unsupported child element: {sorted(unknown)[0]}")
    return LlmSemanticCacheLookup(
        score_threshold=values["score-threshold"],
        embeddings_backend_id=values["embeddings-backend-id"],
        embeddings_backend_auth=values["embeddings-backend-auth"],
        ignore_system_messages=element.attrib.get("ignore-system-messages"),
        max_message_count=element.attrib.get("max-message-count"),
        vary_by=tuple((child.text or "").strip() for child in element.findall("vary-by")),
    )


def _parse_store(element: Element) -> LlmSemanticCacheStore:
    _reject_unknown_attributes(element, {"id", "duration"}, element.tag)
    duration = (element.attrib.get("duration") or "").strip()
    if not duration:
        raise ValueError(f"{element.tag} requires duration")
    if len(element):
        raise ValueError(f"{element.tag} takes no child elements")
    return LlmSemanticCacheStore(duration=duration)


def parse_semantic_cache_policy(element: Element) -> PolicyNode | None:
    if element.tag in LOOKUP_TAGS:
        return _parse_lookup(element)
    if element.tag in STORE_TAGS:
        return _parse_store(element)
    return None
