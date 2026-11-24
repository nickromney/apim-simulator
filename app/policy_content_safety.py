"""llm-content-safety as a thin client of an Azure AI Content Safety endpoint.

The gateway extracts prompt or completion text, sends it to the backend's
``text:analyze`` (and, with shield-prompt, ``text:shieldPrompt``) operations,
and blocks with 403 when a category reaches its threshold, a blocklist
matches, or a prompt attack is detected. Verdicts come from the backend; the
sibling aifoundry-simulator serves this API locally.

https://learn.microsoft.com/en-us/azure/api-management/llm-content-safety-policy
"""

from __future__ import annotations

import json
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
    _policy_int,
    _record_step,
    _reject_unknown_attributes,
    render_policy_value,
)

CONTENT_SAFETY_API_VERSION = "2024-09-01"
CATEGORY_NAMES = {"hate": "Hate", "selfharm": "SelfHarm", "sexual": "Sexual", "violence": "Violence"}
OUTPUT_TYPES = {"FourSeverityLevels", "EightSeverityLevels"}
# Microsoft documents the prompt window as fixed and the response window's default.
PROMPT_WINDOW = 10_000
DEFAULT_RESPONSE_WINDOW = 1_000
# Microsoft documents the 403 status but not its body; this body is local.
BLOCKED_MESSAGES = {
    "inbound": "Request blocked by content safety policy.",
    "outbound": "Response blocked by content safety policy.",
}


@dataclass(frozen=True)
class LlmContentSafety(PolicyNode):
    backend_id: str
    shield_prompt: str | None = None
    enforce_on_completions: str | None = None
    window_size: str | None = None
    window_overlap_size: str | None = None
    output_type: str | None = None
    categories: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    blocklists: tuple[str, ...] = field(default_factory=tuple)

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        raise RuntimeError("llm-content-safety must be executed through apply_async")

    async def apply_async(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        if runtime is None or runtime.http_client is None or runtime.gateway_config is None:
            raise HTTPException(status_code=500, detail="llm-content-safety requires a gateway runtime")
        section = "outbound" if req.section == "outbound" else "inbound"
        text, window, overlap = self._text_and_window(section, req, runtime)
        if not text:
            _record_step(runtime, "llm-content-safety", {"section": section, "status": "no-text"})
            return None
        reason = await self._verdict(section, text, window, overlap, req, runtime)
        _record_step(
            runtime, "llm-content-safety", {"section": section, "blocked": reason is not None, **(reason or {})}
        )
        if reason is None:
            return None
        body = json.dumps({"statusCode": 403, "message": BLOCKED_MESSAGES[section]}).encode("utf-8")
        return ResponseSpec(
            status_code=403,
            headers={"content-type": "application/json"},
            body=body,
            media_type="application/json",
            reason="Forbidden",
        )

    def _text_and_window(self, section: str, req: PolicyRequest, runtime: PolicyRuntime) -> tuple[str, int, int]:
        if section == "outbound":
            window = _policy_int(self.window_size, req, runtime, default=DEFAULT_RESPONSE_WINDOW)
            overlap = _policy_int(self.window_overlap_size, req, runtime, default=0)
            return completion_text(req.response_body or b""), window, overlap
        if _policy_bool(self.enforce_on_completions, req, runtime):
            raise HTTPException(
                status_code=500,
                detail="llm-content-safety enforce-on-completions is not simulated; add the policy to outbound",
            )
        return prompt_text(req.body), PROMPT_WINDOW, 0

    async def _verdict(
        self, section: str, text: str, window: int, overlap: int, req: PolicyRequest, runtime: PolicyRuntime
    ) -> dict[str, Any] | None:
        """The first reason to block: a prompt attack, then each window's analysis."""
        client = _ContentSafetyClient.for_backend(render_policy_value(self.backend_id, req, runtime), req, runtime)
        if section == "inbound" and _policy_bool(self.shield_prompt, req, runtime):
            attack = await client.shield_prompt(text)
            if attack is not None:
                return attack
        thresholds = self._thresholds(req, runtime)
        blocklists = [render_policy_value(item, req, runtime) for item in self.blocklists]
        if not thresholds and not blocklists:
            return None
        output_type = render_policy_value(self.output_type or "FourSeverityLevels", req, runtime)
        if output_type not in OUTPUT_TYPES:
            raise HTTPException(status_code=500, detail=f"llm-content-safety unknown output-type: {output_type}")
        for chunk in _windows(text, window, overlap):
            reason = await client.analyze(chunk, thresholds, output_type, blocklists)
            if reason is not None:
                return reason
        return None

    def _thresholds(self, req: PolicyRequest, runtime: PolicyRuntime | None) -> dict[str, int]:
        thresholds: dict[str, int] = {}
        for raw_name, raw_threshold in self.categories:
            name = CATEGORY_NAMES.get(render_policy_value(raw_name, req, runtime).strip().lower())
            if name is None:
                raise HTTPException(status_code=500, detail=f"llm-content-safety unknown category: {raw_name}")
            threshold = _policy_int(raw_threshold, req, runtime, default=-1)
            if not 0 <= threshold <= 7:
                raise HTTPException(status_code=500, detail="llm-content-safety category threshold must be 0 to 7")
            thresholds[name] = threshold
        return thresholds


@dataclass(frozen=True)
class _ContentSafetyClient:
    client: httpx.AsyncClient
    base_url: str
    headers: list[tuple[str, str]]
    query: dict[str, str]
    auth: Any

    @classmethod
    def for_backend(cls, backend_id: str, req: PolicyRequest, runtime: PolicyRuntime) -> _ContentSafetyClient:
        from app.backend_pool import apply_backend_credentials

        backend = runtime.gateway_config.backends.get(backend_id) if runtime.gateway_config else None
        if backend is None or not backend.url:
            raise HTTPException(status_code=500, detail=f"llm-content-safety unknown backend: {backend_id}")
        callout = PolicyRequest(method="POST", path="/", query={}, headers={}, variables=dict(req.variables))
        auth = apply_backend_credentials(backend, callout, runtime.gateway_config)
        return cls(
            client=runtime.http_client,
            base_url=backend.url.rstrip("/"),
            headers=list(callout.headers.as_header_pairs()),
            query={**callout.query, "api-version": CONTENT_SAFETY_API_VERSION},
            auth=auth,
        )

    async def _post(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self.client.post(
                f"{self.base_url}/contentsafety/{operation}",
                params=self.query,
                headers=self.headers,
                json=payload,
                auth=self.auth or httpx.USE_CLIENT_DEFAULT,
            )
        except httpx.RequestError as exc:
            raise HTTPException(status_code=500, detail=f"llm-content-safety call failed: {exc}") from exc
        if response.status_code != 200:
            raise HTTPException(
                status_code=500, detail=f"llm-content-safety {operation} returned {response.status_code}"
            )
        return response.json()

    async def analyze(
        self, text: str, thresholds: dict[str, int], output_type: str, blocklists: list[str]
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {"text": text, "outputType": output_type}
        if thresholds:
            payload["categories"] = list(thresholds)
        if blocklists:
            payload["blocklistNames"] = blocklists
        result = await self._post("text:analyze", payload)
        if result.get("blocklistsMatch"):
            return {"reason": "blocklist", "blocklist": result["blocklistsMatch"][0].get("blocklistName", "")}
        for item in result.get("categoriesAnalysis") or []:
            category, severity = item.get("category"), int(item.get("severity") or 0)
            if category in thresholds and severity >= thresholds[category]:
                return {"reason": "category", "category": category, "severity": severity}
        return None

    async def shield_prompt(self, text: str) -> dict[str, Any] | None:
        result = await self._post("text:shieldPrompt", {"userPrompt": text, "documents": []})
        if (result.get("userPromptAnalysis") or {}).get("attackDetected"):
            return {"reason": "prompt-attack"}
        return None


def _windows(text: str, size: int, overlap: int) -> list[str]:
    if size <= 0:
        raise HTTPException(status_code=500, detail="llm-content-safety window-size must be positive")
    step = max(1, size - max(0, overlap))
    return [text[start : start + size] for start in range(0, len(text), step)] or [text]


def _text_parts(value: Any) -> list[str]:
    """Text from a string, a list of content parts, or a list of messages."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [part for item in value for part in _text_parts(item)]
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return [value["text"]]
        if "content" in value:
            return _text_parts(value["content"])
    return []


def _json(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def prompt_text(body: bytes) -> str:
    """The prompt in OpenAI chat/completions, Responses or Anthropic Messages requests."""
    payload = _json(body)
    if not isinstance(payload, dict):
        return body.decode("utf-8", errors="replace").strip()
    parts: list[str] = []
    for key in ("system", "instructions", "messages", "prompt", "input"):
        parts.extend(_text_parts(payload.get(key)))
    return "\n".join(part for part in parts if part)


def completion_text(body: bytes) -> str:
    """The completion in OpenAI chat/completions, Responses or Anthropic Messages responses."""
    payload = _json(body)
    if not isinstance(payload, dict):
        return body.decode("utf-8", errors="replace").strip()
    parts: list[str] = []
    for choice in payload.get("choices") or []:
        if isinstance(choice, dict):
            parts.extend(_text_parts(choice.get("message")) or _text_parts(choice.get("text")))
    if isinstance(payload.get("output_text"), str):
        parts.append(payload["output_text"])
    else:
        parts.extend(_text_parts(payload.get("output")))
    parts.extend(_text_parts(payload.get("content")))
    return "\n".join(part for part in parts if part)


def parse_content_safety_policy(element: Element) -> PolicyNode | None:
    if element.tag != "llm-content-safety":
        return None
    _reject_unknown_attributes(
        element,
        {"id", "backend-id", "shield-prompt", "enforce-on-completions", "window-size", "window-overlap-size"},
        "llm-content-safety",
    )
    backend_id = (element.attrib.get("backend-id") or "").strip()
    if not backend_id:
        raise ValueError("llm-content-safety requires backend-id")
    categories_el = element.find("categories")
    categories: list[tuple[str, str]] = []
    if categories_el is not None:
        for category in categories_el.findall("category"):
            name, threshold = category.attrib.get("name"), category.attrib.get("threshold")
            if not name or threshold is None:
                raise ValueError("llm-content-safety category requires name and threshold")
            categories.append((name, threshold))
    blocklists_el = element.find("blocklists")
    blocklists = (
        tuple((item.text or "").strip() for item in blocklists_el.findall("id")) if blocklists_el is not None else ()
    )
    unknown = {child.tag for child in element} - {"categories", "blocklists"}
    if unknown:
        raise ValueError(f"llm-content-safety unsupported child element: {sorted(unknown)[0]}")
    return LlmContentSafety(
        backend_id=backend_id,
        shield_prompt=element.attrib.get("shield-prompt"),
        enforce_on_completions=element.attrib.get("enforce-on-completions"),
        window_size=element.attrib.get("window-size"),
        window_overlap_size=element.attrib.get("window-overlap-size"),
        output_type=categories_el.attrib.get("output-type") if categories_el is not None else None,
        categories=tuple(categories),
        blocklists=tuple(item for item in blocklists if item),
    )
