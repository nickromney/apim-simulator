"""Explicit local broker/identity bindings; credentials come from environment."""

from pydantic import BaseModel, Field


class BrokerBinding(BaseModel):
    endpoint: str
    sender_key_env: str = "BROKER_SENDER_KEY"
    allowed_client_ids: list[str] = Field(default_factory=lambda: ["system-assigned"])


class LocalMessagingConfig(BaseModel):
    namespaces: dict[str, BrokerBinding] = Field(default_factory=dict)
    default_namespace: str | None = None
