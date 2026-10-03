"""Opt-in local governance, audit and ingress controls."""

import ipaddress
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SecurityGovernanceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["disabled", "audit", "deny"] = "disabled"
    encrypted_protocols: bool = False
    backend_certificate_verification: bool = False
    vault_secret_named_values: bool = False
    private_gateway: bool = False
    required_api_tags: list[str] = Field(default_factory=list)
    # Keys are collections such as apis, products, backends or named_values.
    delete_locks: dict[str, list[str]] = Field(default_factory=dict)


class SecurityThreatRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    kinds: list[str] = Field(default_factory=list)
    status_codes: list[int] = Field(default_factory=list)
    threshold: int = Field(default=5, ge=1, le=10000)
    window_seconds: int = Field(default=60, ge=1, le=86400)
    recommendation: str = "Review the caller and tighten authentication or request limits."


class SecurityObservabilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    database_path: str = "/tmp/apim-security-events.sqlite3"
    max_events: int = Field(default=10000, ge=100, le=1000000)
    unused_endpoint_seconds: int = Field(default=86400, ge=1)
    rules: list[SecurityThreatRule] = Field(
        default_factory=lambda: [
            SecurityThreatRule(name="authentication-failures", kinds=["authentication_failure"], threshold=5),
            SecurityThreatRule(name="authorization-failures", kinds=["authorization_failure"], threshold=5),
            SecurityThreatRule(name="request-abuse", kinds=["ingress_denial"], threshold=5),
            SecurityThreatRule(name="validation-probes", kinds=["validation_failure"], threshold=5),
        ]
    )


class SecurityIngressConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    waf_mode: Literal["disabled", "detect", "block"] = "disabled"
    waf_rules: list[Literal["sql-injection", "script-injection", "path-traversal"]] = Field(
        default_factory=lambda: ["sql-injection", "script-injection", "path-traversal"]
    )
    max_body_bytes: int = Field(default=1048576, ge=1, le=16777216)
    body_read_timeout_seconds: float = Field(default=10, gt=0, le=300)
    max_query_bytes: int = Field(default=8192, ge=1, le=65536)
    max_concurrent_requests: int = Field(default=100, ge=1, le=10000)
    requests_per_window: int = Field(default=1000, ge=1, le=1000000)
    window_seconds: int = Field(default=60, ge=1, le=3600)
    max_rate_keys: int = Field(default=4096, ge=1, le=100000)
    max_rate_entries: int = Field(default=100000, ge=1, le=1000000)
    allowed_client_networks: list[str] = Field(default_factory=list)
    exclude_prefixes: list[str] = Field(default_factory=lambda: ["/apim/"])

    @field_validator("allowed_client_networks")
    @classmethod
    def validate_networks(cls, values):
        return [str(ipaddress.ip_network(value, strict=False)) for value in values]
