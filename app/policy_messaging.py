"""Local broker adapters for documented messaging policies."""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx
from fastapi import HTTPException

from app.messaging_config import BrokerBinding
from app.policy import PolicyNode

if TYPE_CHECKING:
    from xml.etree.ElementTree import Element

    from app.policy import PolicyRequest, PolicyRuntime


def _ttl(value: str | None) -> float | None:
    if value is None:
        return None
    match = re.fullmatch(r"(?:(\d+)\.)?(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d{1,7}))?", value)
    if not match:
        raise ValueError("time-to-live must be a positive TimeSpan such as 00:10:00")
    days, hours, minutes, seconds, fraction = match.groups()
    total = (
        int(days or 0) * 86400 + int(hours) * 3600 + int(minutes) * 60 + int(seconds) + float("0." + (fraction or "0"))
    )
    if int(hours) >= 24 or int(minutes) >= 60 or int(seconds) >= 60 or total <= 0:
        raise ValueError("time-to-live must be a positive TimeSpan")
    return total


def _binding(runtime: PolicyRuntime | None, namespace: str | None, client_id: str | None = None) -> BrokerBinding:
    if runtime is None or runtime.gateway_config is None:
        raise HTTPException(500, "Messaging requires a configured local broker")
    settings = runtime.gateway_config.local_messaging
    binding = settings.namespaces.get(namespace or settings.default_namespace or "")
    if binding is None:
        raise HTTPException(500, "No local broker binding is configured for this namespace")
    if (client_id or "system-assigned") not in binding.allowed_client_ids:
        raise HTTPException(500, "Local identity is not granted sender access to this namespace")
    return binding


async def _publish(runtime: PolicyRuntime, binding: BrokerBinding, kind: str, name: str, message: dict) -> None:
    key = os.environ.get(binding.sender_key_env)
    if not key:
        raise HTTPException(500, "Local broker sender access key is not configured")
    if runtime.http_client is None:
        raise HTTPException(500, "Messaging requires an HTTP client")
    response = await runtime.http_client.post(
        binding.endpoint.rstrip("/") + f"/{kind}/{quote(name, safe='')}/messages",
        headers={"Authorization": "Bearer " + key},
        json=message,
        timeout=runtime.timeout_seconds,
    )
    response.raise_for_status()


@dataclass
class SendServiceBusMessage(PolicyNode):
    attributes: dict[str, str]
    payload: str
    properties: dict[str, str] = field(default_factory=dict)

    async def apply_async(self, req: PolicyRequest, runtime: PolicyRuntime | None = None):
        from app.policy import render_policy_value

        attrs = {key: render_policy_value(value, req, runtime) for key, value in self.attributes.items()}
        try:
            message_id = str(uuid.UUID(attrs["message-id"])) if attrs.get("message-id") else str(uuid.uuid4())
            session_id = str(uuid.UUID(attrs["session-id"])) if attrs.get("session-id") else None
            ttl = _ttl(attrs.get("time-to-live"))
        except ValueError as exc:
            raise HTTPException(500, f"Invalid send-service-bus-message configuration: {exc}") from exc
        message = {
            "message_id": message_id,
            "session_id": session_id,
            "ttl_seconds": ttl,
            "properties": {key: render_policy_value(value, req, runtime) for key, value in self.properties.items()},
            "payload": render_policy_value(self.payload, req, runtime),
        }
        variable = attrs.get("response-variable-name")
        try:
            binding = _binding(runtime, attrs.get("namespace"), attrs.get("client-id"))
            await _publish(
                runtime,
                binding,
                "queues" if "queue-name" in attrs else "topics",
                attrs.get("queue-name") or attrs["topic-name"],
                message,
            )
        except (httpx.HTTPError, HTTPException) as exc:
            if attrs.get("ignore-error", "false").lower() != "true":
                raise HTTPException(500, f"send-service-bus-message failed: {exc}") from exc
            if variable:
                req.variables[variable] = {"Error": {"Reason": "SendFailed", "Message": str(exc)}}
            return None
        if variable:
            req.variables[variable] = {
                "MessageId": message_id,
                "SessionId": session_id,
                "TimeToLive": attrs.get("time-to-live"),
            }
        return None


@dataclass
class LogToEventHub(PolicyNode):
    logger_id: str
    payload: str
    partition_id: str | None = None
    partition_key: str | None = None

    async def apply_async(self, req: PolicyRequest, runtime: PolicyRuntime | None = None):
        from app.policy import render_policy_value

        cfg = runtime.gateway_config if runtime is not None else None
        logger = cfg.loggers.get(self.logger_id) if cfg else None
        if logger is None or logger.eventhub is None:
            raise HTTPException(500, "log-to-eventhub requires an Event Hub logger")
        binding = _binding(runtime, logger.eventhub.endpoint_uri, logger.eventhub.user_assigned_identity_client_id)
        payload = (
            render_policy_value(self.payload, req, runtime)
            .encode("utf-8")[: 200 * 1024]
            .decode("utf-8", errors="ignore")
        )
        message = {
            "payload": payload,
            "message_id": str(uuid.uuid4()),
            "partition_id": self.partition_id,
            "partition_key": render_policy_value(self.partition_key, req, runtime) if self.partition_key else None,
        }
        try:
            await _publish(runtime, binding, "topics", logger.eventhub.name, message)
        except httpx.HTTPError as exc:
            raise HTTPException(500, f"log-to-eventhub failed: {exc}") from exc
        return None


@dataclass
class SendOneWayRequest(PolicyNode):
    request: object

    async def apply_async(self, req: PolicyRequest, runtime: PolicyRuntime | None = None):
        # Assemble before scheduling: malformed configuration fails normally.
        url, method, message = self.request._build_callout_request(req, runtime)
        client = runtime.http_client if runtime else None
        if client is None:
            raise HTTPException(500, "send-one-way-request requires an HTTP client")
        from app.policy import render_policy_value

        timeout = float(render_policy_value(self.request.timeout, req, runtime)) if self.request.timeout else 60.0
        if timeout <= 0:
            raise HTTPException(500, "send-one-way-request timeout must be positive")
        task = asyncio.create_task(
            _one_way(
                client,
                url,
                method,
                [(key, value) for key, value in message.headers.as_header_pairs() if key.lower() != "content-length"],
                message.body,
                timeout,
            )
        )
        runtime.background_tasks.add(task)
        task.add_done_callback(runtime.background_tasks.discard)
        return None


async def _one_way(client, url, method, headers, body, timeout) -> None:
    try:
        await client.request(method, url, headers=headers, content=body, timeout=timeout)
    except httpx.HTTPError:
        pass  # Fire-and-forget failures do not alter the original response.


def parse_messaging_policy(element: Element):
    from app.policy import _parse_send_request

    if element.tag == "send-one-way-request":
        from copy import deepcopy

        copied = deepcopy(element)
        copied.set("response-variable-name", "__one_way")
        return SendOneWayRequest(_parse_send_request(copied))
    if element.tag == "log-to-eventhub":
        if set(element.attrib) - {"id", "logger-id", "partition-id", "partition-key"} or list(element):
            raise ValueError("Unsupported log-to-eventhub attributes or children")
        logger = element.get("logger-id")
        if not logger or (element.get("partition-id") and element.get("partition-key")):
            raise ValueError("log-to-eventhub needs logger-id and only one partition selector")
        return LogToEventHub(logger, element.text or "", element.get("partition-id"), element.get("partition-key"))
    if element.tag == "send-service-bus-message":
        return _parse_service_bus_message(element)
    return None


def _parse_message_properties(element: Element) -> dict[str, str]:
    properties_element = element.find("message-properties")
    if properties_element is None:
        return {}
    properties = {}
    for prop in properties_element:
        if prop.tag != "message-property" or set(prop.attrib) != {"name"} or list(prop):
            raise ValueError("Message properties must be named text message-property elements")
        name = prop.get("name")
        if not name:
            raise ValueError("Message properties require names")
        if name in properties:
            raise ValueError("Message property names must be unique")
        properties[name] = prop.text or ""
    return properties


def _parse_service_bus_message(element: Element) -> SendServiceBusMessage:
    allowed = {
        "id",
        "queue-name",
        "topic-name",
        "namespace",
        "client-id",
        "message-id",
        "session-id",
        "time-to-live",
        "response-variable-name",
        "ignore-error",
    }
    if set(element.attrib) - allowed:
        raise ValueError("Unsupported send-service-bus-message attribute")
    children = [child.tag for child in element]
    if children not in [["payload"], ["message-properties", "payload"]]:
        raise ValueError("Use optional message-properties followed by exactly one payload")
    if bool(element.get("queue-name")) == bool(element.get("topic-name")):
        raise ValueError("Specify exactly one queue-name or topic-name")
    payload = element.find("payload")
    return SendServiceBusMessage(dict(element.attrib), payload.text or "", _parse_message_properties(element))
