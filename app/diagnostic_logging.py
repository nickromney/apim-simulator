"""Apply APIM diagnostic settings to local request telemetry, and the trace policy.

References:
https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-app-insights
https://learn.microsoft.com/en-us/azure/api-management/diagnostic-logs-reference
https://learn.microsoft.com/en-us/azure/api-management/trace-policy
https://learn.microsoft.com/en-us/rest/api/apimanagement/diagnostic/create-or-update

A diagnostic named `applicationinsights` or `azuremonitor`, at service scope or
for one API, decides what the gateway logs for each request: the sampling
rate, whether errors bypass sampling, whether the client IP is kept, which
headers and how many body bytes are kept at each of the four stages (frontend
request, backend request, backend response, frontend response), masking, the
correlation protocol and which `trace` policy messages are kept. An API-scope
diagnostic replaces the service-scope one for that API.

Locally the records go to an in-memory store read through the management API,
instead of to Application Insights or Log Analytics.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.etree.ElementTree import Element

from fastapi import HTTPException

from app.config import DiagnosticConfig, DiagnosticHttpMessageConfig, GatewayConfig
from app.policy import PolicyNode, PolicyRequest, PolicyRuntime, _record_step, render_policy_value

DIAGNOSTIC_IDENTIFIERS = ("applicationinsights", "azuremonitor")
MAX_BODY_BYTES = 8192
STAGES = ("frontend_request", "backend_request", "backend_response", "frontend_response")
SEVERITIES = ("verbose", "information", "error")
CORRELATION_PROTOCOLS = ("None", "Legacy", "W3C")
MASKING_MODES = ("Mask", "Hide")
_MASK = "***"
_CAPTURE_STATE = "apim_diagnostic_capture"


def diagnostics_active(cfg: GatewayConfig) -> bool:
    return bool(cfg.diagnostics)


def api_diagnostic_key(api_id: str, identifier: str) -> str:
    return f"{api_id}:{identifier}"


def validate_diagnostic(cfg: GatewayConfig, diagnostic_id: str, diagnostic: DiagnosticConfig) -> None:
    """Reject settings Azure refuses, so local edits stay deployable."""
    problems = _diagnostic_problems(cfg, diagnostic_id, diagnostic)
    if problems:
        raise HTTPException(status_code=400, detail="; ".join(problems))


def _diagnostic_problems(cfg: GatewayConfig, diagnostic_id: str, diagnostic: DiagnosticConfig) -> list[str]:
    problems: list[str] = []
    if diagnostic_id not in DIAGNOSTIC_IDENTIFIERS:
        problems.append(f"diagnostic id must be one of {', '.join(DIAGNOSTIC_IDENTIFIERS)}")
    if diagnostic.logger_id and diagnostic.logger_id not in cfg.loggers:
        problems.append(f"logger {diagnostic.logger_id} does not exist")
    if diagnostic.sampling_percentage is not None and not 0 <= diagnostic.sampling_percentage <= 100:
        problems.append("sampling_percentage must be between 0 and 100")
    if diagnostic.verbosity is not None and diagnostic.verbosity not in SEVERITIES:
        problems.append(f"verbosity must be one of {', '.join(SEVERITIES)}")
    if (
        diagnostic.http_correlation_protocol is not None
        and diagnostic.http_correlation_protocol not in CORRELATION_PROTOCOLS
    ):
        problems.append(f"http_correlation_protocol must be one of {', '.join(CORRELATION_PROTOCOLS)}")
    for stage in STAGES:
        problems.extend(_message_problems(stage, getattr(diagnostic, stage)))
    return problems


def _message_problems(stage: str, message: DiagnosticHttpMessageConfig | None) -> list[str]:
    if message is None:
        return []
    problems = []
    if message.body_bytes is not None and not 0 <= message.body_bytes <= MAX_BODY_BYTES:
        problems.append(f"{stage}.body_bytes must be between 0 and {MAX_BODY_BYTES}")
    masking = message.data_masking
    rules = [*masking.headers, *masking.query_params] if masking else []
    if any(rule.mode not in MASKING_MODES for rule in rules):
        problems.append(f"{stage} data masking mode must be Mask or Hide")
    return problems


def effective_diagnostics(cfg: GatewayConfig, api_id: str | None) -> list[tuple[str, DiagnosticConfig]]:
    """One diagnostic per destination: the API's own, else the service's."""
    chosen = []
    for identifier in DIAGNOSTIC_IDENTIFIERS:
        api_level = cfg.diagnostics.get(api_diagnostic_key(api_id, identifier)) if api_id else None
        service_level = cfg.diagnostics.get(identifier)
        diagnostic = api_level or (service_level if service_level and not service_level.api_id else None)
        if diagnostic is not None:
            chosen.append((identifier, diagnostic))
    return chosen


# --- capture ----------------------------------------------------------------


def capture_stage(state: Any, cfg: GatewayConfig, stage: str, *, headers: Any, body: bytes | None) -> None:
    """Keep what one stage saw; nothing is kept when no diagnostic is configured."""
    if not diagnostics_active(cfg):
        return
    captured = getattr(state, _CAPTURE_STATE, None)
    if captured is None:
        captured = {}
        setattr(state, _CAPTURE_STATE, captured)
    pairs = headers.multi_items() if hasattr(headers, "multi_items") else list(dict(headers).items())
    captured[stage] = {"headers": [(str(k), str(v)) for k, v in pairs], "body": body}


def _captured(state: Any) -> dict[str, Any]:
    return getattr(state, _CAPTURE_STATE, None) or {}


def _headers_to_log(pairs: list[tuple[str, str]], message: DiagnosticHttpMessageConfig) -> dict[str, str]:
    wanted = {name.lower() for name in message.headers_to_log}
    rules = {rule.value.lower(): rule.mode for rule in (message.data_masking.headers if message.data_masking else [])}
    logged: dict[str, str] = {}
    for name, value in pairs:
        key = name.lower()
        if key not in wanted or rules.get(key) == "Hide":
            continue
        logged[key] = _MASK if rules.get(key) == "Mask" else value
    return logged


def _body_to_log(body: bytes | None, message: DiagnosticHttpMessageConfig) -> dict[str, Any]:
    limit = min(message.body_bytes or 0, MAX_BODY_BYTES)
    if not limit:
        return {}
    if body is None:
        return {"body_unavailable": "streamed"}
    return {"body": body[:limit].decode("utf-8", errors="replace"), "body_truncated": len(body) > limit}


def _message_record(captured: dict[str, Any], message: DiagnosticHttpMessageConfig | None) -> dict[str, Any] | None:
    if message is None or not captured:
        return None
    record: dict[str, Any] = {}
    headers = _headers_to_log(captured["headers"], message)
    if headers:
        record["headers"] = headers
    record.update(_body_to_log(captured["body"], message))
    return record or None


def mask_url(url: str, diagnostic: DiagnosticConfig) -> str:
    """Apply frontend-request query masking to the logged URL."""
    message = diagnostic.frontend_request
    rules = {
        rule.value: rule.mode
        for rule in (message.data_masking.query_params if message and message.data_masking else [])
    }
    if not rules:
        return url
    parts = urlsplit(url)
    query = [
        (key, _MASK if rules.get(key) == "Mask" else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if rules.get(key) != "Hide"
    ]
    return urlunsplit(parts._replace(query=urlencode(query, safe=_MASK)))


# --- records ----------------------------------------------------------------


@dataclass
class RequestOutcome:
    method: str
    url: str
    status_code: int
    duration_ms: float
    api_id: str | None
    operation_id: str | None
    client_ip: str | None
    request_headers: dict[str, str]


def _sampled_in(counter: int, diagnostic: DiagnosticConfig) -> bool:
    """Deterministic sampling, the same scheme as local gateway logs."""
    percentage = 100.0 if diagnostic.sampling_percentage is None else diagnostic.sampling_percentage
    return (counter * 37) % 100 < percentage


def _correlation(diagnostic: DiagnosticConfig, headers: dict[str, str]) -> dict[str, str]:
    protocol = diagnostic.http_correlation_protocol or "Legacy"
    if protocol == "W3C":
        traceparent = headers.get("traceparent", "")
        parts = traceparent.split("-")
        operation_id = parts[1] if len(parts) == 4 and len(parts[1]) == 32 else uuid.uuid4().hex
        return {"correlation_protocol": "W3C", "operation_id": operation_id}
    if protocol == "Legacy":
        request_id = headers.get("request-id", "")
        operation_id = request_id.lstrip("|").split(".", 1)[0] if request_id else uuid.uuid4().hex
        return {"correlation_protocol": "Legacy", "operation_id": operation_id}
    return {"correlation_protocol": "None"}


def _traces_at_or_above(traces: list[dict[str, Any]], verbosity: str | None) -> list[dict[str, Any]]:
    floor = SEVERITIES.index(verbosity) if verbosity in SEVERITIES else SEVERITIES.index("information")
    return [trace for trace in traces if SEVERITIES.index(trace["severity"]) >= floor]


def build_telemetry(
    state: Any,
    cfg: GatewayConfig,
    outcome: RequestOutcome,
    *,
    counter: int,
    traces: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """The records each effective diagnostic keeps for this request."""
    records = []
    captured = _captured(state)
    for identifier, diagnostic in effective_diagnostics(cfg, outcome.api_id):
        failed = outcome.status_code >= 400
        if not _sampled_in(counter, diagnostic) and not (failed and diagnostic.always_log_errors):
            continue
        record: dict[str, Any] = {
            "destination": identifier,
            "diagnostic_scope": "api" if diagnostic.api_id else "service",
            "logger_id": diagnostic.logger_id,
            "time": datetime.now(UTC).isoformat(),
            "method": outcome.method,
            "url": mask_url(outcome.url, diagnostic),
            "status_code": outcome.status_code,
            "success": not failed,
            "duration_ms": outcome.duration_ms,
            "api_id": outcome.api_id,
            "operation_id": outcome.operation_id,
            **_correlation(diagnostic, outcome.request_headers),
        }
        if diagnostic.log_client_ip:
            record["client_ip"] = outcome.client_ip
        for stage in STAGES:
            stage_record = _message_record(captured.get(stage, {}), getattr(diagnostic, stage))
            if stage_record:
                record[stage] = stage_record
        kept = _traces_at_or_above(traces, diagnostic.verbosity)
        if kept:
            record["traces"] = kept
        records.append(record)
    return records


# --- trace policy -----------------------------------------------------------


@dataclass(frozen=True)
class Trace(PolicyNode):
    """Write a message to the request trace and to diagnostic telemetry.

    https://learn.microsoft.com/en-us/azure/api-management/trace-policy
    """

    source: str
    message: str
    severity: str = "verbose"
    metadata: tuple[tuple[str, str], ...] = ()

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> None:
        entry = {
            "source": render_policy_value(self.source, req, runtime),
            "severity": self.severity,
            "message": render_policy_value(self.message, req, runtime),
            "metadata": {name: render_policy_value(value, req, runtime) for name, value in self.metadata},
        }
        traces = req.variables.setdefault("_diagnostic_traces", [])
        if isinstance(traces, list):
            traces.append(entry)
        _record_step(runtime, "trace", entry)


def _trace_metadata(element: Element) -> tuple[tuple[str, str], ...]:
    metadata = []
    for child in element:
        if child.tag == "metadata":
            name, value = child.attrib.get("name"), child.attrib.get("value")
            if not name or value is None:
                raise ValueError("trace metadata requires name and value")
            metadata.append((name, value))
        elif child.tag != "message":
            raise ValueError(f"trace does not allow {child.tag}")
    return tuple(metadata)


def _trace_attributes(element: Element) -> tuple[str, str]:
    unknown = set(element.attrib) - {"source", "severity", "id"}
    if unknown:
        raise ValueError(f"trace unsupported attributes: {', '.join(sorted(unknown))}")
    source = (element.attrib.get("source") or "").strip()
    if not source:
        raise ValueError("trace requires source")
    severity = (element.attrib.get("severity") or "verbose").strip().lower()
    if severity not in SEVERITIES:
        raise ValueError("trace severity must be verbose, information or error")
    return source, severity


def parse_trace_policy(element: Element) -> PolicyNode | None:
    if element.tag != "trace":
        return None
    source, severity = _trace_attributes(element)
    message_el = element.find("message")
    if message_el is None or not (message_el.text or "").strip():
        raise ValueError("trace requires a message")
    return Trace(source=source, message=message_el.text or "", severity=severity, metadata=_trace_metadata(element))
