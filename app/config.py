from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.urls import http_url

# APIM creates this global backend policy when no global policy document exists.
# https://learn.microsoft.com/en-us/azure/api-management/set-edit-policies
DEFAULT_GLOBAL_POLICY_XML = "<policies><backend><forward-request /></backend></policies>"


class ApiVersioningScheme(StrEnum):
    Header = "Header"
    Query = "Query"
    Segment = "Segment"


class ApiVersionSetConfig(BaseModel):
    # Mirrors the ARM shape for Microsoft.ApiManagement/service/api-version-sets.
    # https://learn.microsoft.com/en-us/azure/templates/microsoft.apimanagement/service/api-version-sets
    display_name: str
    description: str | None = None
    versioning_scheme: ApiVersioningScheme
    version_header_name: str | None = None
    version_query_name: str | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.versioning_scheme == ApiVersioningScheme.Header and not self.version_header_name:
            raise ValueError("version_header_name is required when versioning_scheme=Header")
        if self.versioning_scheme == ApiVersioningScheme.Query and not self.version_query_name:
            raise ValueError("version_query_name is required when versioning_scheme=Query")


class ServiceMetadataConfig(BaseModel):
    name: str = "apim-simulator"
    display_name: str = "Local APIM Simulator"
    public_network_access_enabled: bool | None = None
    virtual_network_type: str | None = None
    hostname_configurations: list[ServiceHostnameConfiguration] = Field(default_factory=list)


class ServiceHostnameConfiguration(BaseModel):
    type: str
    host_name: str
    negotiate_client_certificate: bool = False
    default_ssl_binding: bool = False


class HeaderCondition(BaseModel):
    header: str
    starts_with: str | None = None
    equals: str | None = None

    def matches(self, headers: Any) -> bool:
        value = headers.get(self.header)
        if value is None:
            return False
        if self.equals is not None:
            return value == self.equals
        if self.starts_with is not None:
            return value.startswith(self.starts_with)
        return False


class SubscriptionIdentity(BaseModel):
    id: str
    name: str


class SubscriptionKeyPair(BaseModel):
    primary: str
    secondary: str


class SubscriptionState(StrEnum):
    Active = "active"
    Suspended = "suspended"
    Cancelled = "cancelled"
    Submitted = "submitted"
    Rejected = "rejected"
    Expired = "expired"


class SubscriptionScope(StrEnum):
    Product = "product"
    Api = "api"
    AllApis = "all-apis"
    Service = "service"


class Subscription(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    keys: SubscriptionKeyPair
    state: SubscriptionState = SubscriptionState.Active
    # APIM documents these scope categories, but not a local JSON schema for
    # representing them. The simulator keeps product scope backward compatible
    # and makes the other scopes explicit fields.
    products: list[str] = Field(default_factory=list)
    scope: SubscriptionScope | None = None
    api_id: str | None = None
    all_apis: bool = False
    # The built-in APIM all-access subscription is never created implicitly.
    service_scoped: bool = False
    created_by: str | None = None

    def _selected_scopes(self) -> list[SubscriptionScope]:
        selectors: list[SubscriptionScope] = []
        if self.products:
            selectors.append(SubscriptionScope.Product)
        if self.api_id:
            selectors.append(SubscriptionScope.Api)
        if self.all_apis:
            selectors.append(SubscriptionScope.AllApis)
        if self.service_scoped:
            selectors.append(SubscriptionScope.Service)
        return selectors

    def _validate_explicit_scope(self) -> None:
        if self.scope == SubscriptionScope.Product and not self.products:
            raise ValueError("product subscription scope requires products")
        if self.scope == SubscriptionScope.Api and not self.api_id:
            raise ValueError("api subscription scope requires api_id")
        if self.scope == SubscriptionScope.AllApis:
            self.all_apis = True
        if self.scope == SubscriptionScope.Service:
            self.service_scoped = True

    @model_validator(mode="after")
    def _validate_scope(self) -> Subscription:
        selected = self._selected_scopes()
        if len(selected) > 1:
            raise ValueError("subscription scope fields are mutually exclusive")
        if self.scope is None:
            if selected:
                self.scope = selected[0]
            return self
        if selected and selected[0] != self.scope:
            raise ValueError("subscription scope conflicts with its scope fields")
        self._validate_explicit_scope()
        return self

    @property
    def scope_kind(self) -> SubscriptionScope | None:
        return self.scope

    def identity(self) -> SubscriptionIdentity:
        return SubscriptionIdentity(id=self.id, name=self.name)


class SubscriptionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: bool = True
    header_names: list[str] = Field(default_factory=lambda: ["Ocp-Apim-Subscription-Key"])
    query_param_names: list[str] = Field(default_factory=lambda: ["subscription-key"])
    # Back-compat/simple mode: direct map of key -> identity
    keys: dict[str, SubscriptionIdentity] = Field(default_factory=dict)
    # APIM-style: subscriptions are named containers, each with 2 keys
    subscriptions: dict[str, Subscription] = Field(default_factory=dict)
    bypass: list[HeaderCondition] = Field(default_factory=list)

    def find_entry(self, subscription_id: str) -> tuple[str, Subscription] | None:
        for config_key, sub in self.subscriptions.items():
            if sub.id == subscription_id:
                return config_key, sub
        return None

    def find_by_id(self, subscription_id: str) -> Subscription | None:
        entry = self.find_entry(subscription_id)
        return entry[1] if entry is not None else None

    def lookup_subscription_by_key(self, key: str) -> Subscription | None:
        for sub in self.subscriptions.values():
            if key == sub.keys.primary or key == sub.keys.secondary:
                return sub
        return None

    def lookup_identity_by_key(self, key: str) -> SubscriptionIdentity | None:
        identity = self.keys.get(key)
        if identity is not None:
            return identity
        sub = self.lookup_subscription_by_key(key)
        return sub.identity() if sub is not None else None


class OIDCConfig(BaseModel):
    issuer: str
    audience: str
    jwks_uri: str | None = None
    jwks: dict[str, Any] | None = None


class PortalConfig(BaseModel):
    enabled: bool = False
    user_header: str = "X-Apim-Portal-User"


class TenantAccessConfig(BaseModel):
    enabled: bool = False
    primary_key: str | None = None
    secondary_key: str | None = None


class ClientCertificateMode(StrEnum):
    """Maps to Azure APIM client certificate settings.

    - disabled: No client cert required (default)
    - optional: Client cert accepted but not required (negotiate_client_certificate=true)
    - required: Client cert required for all requests (client_certificate_enabled=true, Consumption SKU)
    """

    Disabled = "disabled"
    Optional = "optional"
    Required = "required"


class TrustedClientCertificateConfig(BaseModel):
    """A trusted client CA or leaf certificate for mTLS validation."""

    name: str
    subject: str | None = None
    issuer: str | None = None
    thumbprint: str | None = None


class ClientCertificateConfig(BaseModel):
    """Client certificate (mTLS) settings for the gateway.

    Maps to Azure APIM settings:
    - client_certificate_enabled (Consumption SKU): requires client cert
    - negotiate_client_certificate (hostname_configuration): optional client cert

    When running behind a TLS-terminating proxy (nginx, envoy, AppGW), the proxy
    forwards cert details via headers:
    - X-Client-Cert-Subject: CN=client,O=org
    - X-Client-Cert-Issuer: CN=ca,O=org
    - X-Client-Cert-Thumbprint: SHA1 fingerprint
    - X-Client-Cert: Base64-encoded DER or PEM

    The simulator validates these headers against trusted_certificates when mode != disabled.
    """

    mode: ClientCertificateMode = ClientCertificateMode.Disabled
    trusted_certificates: list[TrustedClientCertificateConfig] = Field(default_factory=list)
    # Header names (configurable to match your proxy)
    subject_header: str = "X-Client-Cert-Subject"
    issuer_header: str = "X-Client-Cert-Issuer"
    thumbprint_header: str = "X-Client-Cert-Thumbprint"
    cert_header: str = "X-Client-Cert"


class RouteAuthzConfig(BaseModel):
    required_scopes: list[str] = Field(default_factory=list)
    required_roles: list[str] = Field(default_factory=list)
    required_claims: dict[str, str] = Field(default_factory=dict)


class OperationExampleConfig(BaseModel):
    name: str
    summary: str | None = None
    description: str | None = None
    value: Any | None = None
    external_value: str | None = None


class OperationParameterConfig(BaseModel):
    name: str
    required: bool
    type: str
    description: str | None = None
    default_value: str | None = None
    values: list[str] = Field(default_factory=list)
    examples: list[OperationExampleConfig] = Field(default_factory=list)
    schema_id: str | None = None
    type_name: str | None = None


class OperationRepresentationConfig(BaseModel):
    content_type: str
    form_parameters: list[OperationParameterConfig] = Field(default_factory=list)
    examples: list[OperationExampleConfig] = Field(default_factory=list)
    schema_id: str | None = None
    type_name: str | None = None


class OperationRequestMetadataConfig(BaseModel):
    description: str | None = None
    headers: list[OperationParameterConfig] = Field(default_factory=list)
    query_parameters: list[OperationParameterConfig] = Field(default_factory=list)
    representations: list[OperationRepresentationConfig] = Field(default_factory=list)


class OperationResponseMetadataConfig(BaseModel):
    status_code: int
    description: str | None = None
    headers: list[OperationParameterConfig] = Field(default_factory=list)
    representations: list[OperationRepresentationConfig] = Field(default_factory=list)


class ApiSchemaConfig(BaseModel):
    content_type: str
    value: str | None = None
    definitions: dict[str, Any] = Field(default_factory=dict)
    components: dict[str, Any] = Field(default_factory=dict)


class ApiRevisionConfig(BaseModel):
    revision: str
    description: str | None = None
    is_current: bool | None = None
    is_online: bool | None = None
    source_api_id: str | None = None


class ApiReleaseConfig(BaseModel):
    name: str
    api_id: str | None = None
    notes: str | None = None
    revision: str | None = None


class KeyVaultNamedValueConfig(BaseModel):
    secret_id: str
    identity_client_id: str | None = None


class NamedValueConfig(BaseModel):
    value: str | None = None
    secret: bool = False
    value_from_key_vault: KeyVaultNamedValueConfig | None = None


class LoggerApplicationInsightsConfig(BaseModel):
    connection_string: str | None = None
    instrumentation_key: str | None = None


class LoggerEventHubConfig(BaseModel):
    name: str
    connection_string: str | None = None
    endpoint_uri: str | None = None
    user_assigned_identity_client_id: str | None = None


class LoggerConfig(BaseModel):
    logger_type: str = "custom"
    description: str | None = None
    buffered: bool = True
    resource_id: str | None = None
    application_insights: LoggerApplicationInsightsConfig | None = None
    eventhub: LoggerEventHubConfig | None = None


class DiagnosticMaskingRuleConfig(BaseModel):
    mode: str
    value: str


class DiagnosticDataMaskingConfig(BaseModel):
    query_params: list[DiagnosticMaskingRuleConfig] = Field(default_factory=list)
    headers: list[DiagnosticMaskingRuleConfig] = Field(default_factory=list)


class DiagnosticHttpMessageConfig(BaseModel):
    body_bytes: int | None = None
    headers_to_log: list[str] = Field(default_factory=list)
    data_masking: DiagnosticDataMaskingConfig | None = None


class DiagnosticConfig(BaseModel):
    identifier: str
    logger_id: str | None = None
    always_log_errors: bool | None = None
    backend_request: DiagnosticHttpMessageConfig | None = None
    backend_response: DiagnosticHttpMessageConfig | None = None
    frontend_request: DiagnosticHttpMessageConfig | None = None
    frontend_response: DiagnosticHttpMessageConfig | None = None
    http_correlation_protocol: str | None = None
    log_client_ip: bool | None = None
    sampling_percentage: float | None = None
    verbosity: str | None = None
    operation_name_format: str | None = None


class UserConfig(BaseModel):
    id: str
    email: str | None = None
    name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    note: str | None = None
    state: str | None = None
    confirmation: str | None = None


class GroupConfig(BaseModel):
    id: str
    name: str
    description: str | None = None
    external_id: str | None = None
    type: str = "custom"
    users: list[str] = Field(default_factory=list)


class TagConfig(BaseModel):
    display_name: str


class ProductState(StrEnum):
    Published = "published"
    NotPublished = "not_published"


class ProductConfig(BaseModel):
    name: str
    description: str | None = None
    # Azure defaults new products to notPublished; config-authored products
    # default to published so existing configs keep working unchanged.
    state: ProductState = ProductState.Published
    require_subscription: bool = True
    approval_required: bool = False
    subscriptions_limit: int | None = None
    groups: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    policies_xml: str | None = None

    @model_validator(mode="after")
    def _approval_requires_subscription(self) -> ProductConfig:
        if self.approval_required and not self.require_subscription:
            raise ValueError("approval_required is only valid when require_subscription is true")
        return self


class BackendPoolMemberConfig(BaseModel):
    # Mirrors Microsoft.ApiManagement/service/backends pool.services entries.
    backend_id: str
    weight: int = 1
    priority: int = 1

    @model_validator(mode="before")
    @classmethod
    def _map_arm_id(cls, value: Any) -> Any:
        if isinstance(value, dict) and "backend_id" not in value and value.get("id"):
            backend_id = str(value["id"]).rstrip("/").rsplit("/", 1)[-1]
            return {**value, "backend_id": backend_id}
        return value


class BackendCodeRange(BaseModel):
    """Inclusive HTTP status range used by a backend failure condition."""

    min: int
    max: int


def _duration_seconds(value: Any) -> Any:
    if not isinstance(value, str) or not value.upper().startswith("PT"):
        return value
    match = re.fullmatch(
        r"PT(?:(?P<hours>\d+(?:\.\d+)?)H)?(?:(?P<minutes>\d+(?:\.\d+)?)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?",
        value.upper(),
    )
    if match is None:
        return value
    return (
        float(match.group("hours") or 0) * 3600
        + float(match.group("minutes") or 0) * 60
        + float(match.group("seconds") or 0)
    )


class BackendSessionIdConfig(BaseModel):
    """Cookie source used for backend-pool session awareness."""

    source: str = "Cookie"
    name: str

    @model_validator(mode="after")
    def _cookie_only(self) -> BackendSessionIdConfig:
        if self.source.casefold() != "cookie":
            raise ValueError("backend pool session affinity only supports Cookie source")
        return self


class BackendSessionAffinityConfig(BaseModel):
    """APIM's pool sessionAffinity.sessionId configuration."""

    session_id: BackendSessionIdConfig

    @model_validator(mode="before")
    @classmethod
    def _map_arm_session_id(cls, value: Any) -> Any:
        if isinstance(value, dict) and "session_id" not in value and value.get("sessionId"):
            return {**value, "session_id": value["sessionId"]}
        return value


class BackendCircuitBreakerConfig(BaseModel):
    # Adapted from the ARM backend circuitBreaker.rules shape: trip after
    # failure_count errors inside interval_seconds, stay open for
    # trip_duration_seconds.
    failure_count: int = 3
    interval_seconds: float = 60.0
    trip_duration_seconds: float = 30.0
    status_code_ranges: list[BackendCodeRange] = Field(default_factory=list)
    error_reasons: list[str] = Field(default_factory=list)
    accept_retry_after: bool = False
    # Legacy exact statuses remain supported and are used when no ranges exist.
    error_statuses: list[int] = Field(default_factory=lambda: [500, 502, 503, 504])

    @model_validator(mode="before")
    @classmethod
    def _map_arm_rule(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        mapped = dict(value)
        failure = mapped.get("failureCondition") or mapped.get("failure_condition")
        if isinstance(failure, dict):
            mapped = {**failure, **mapped}
        aliases = {
            "count": "failure_count",
            "trip_threshold": "failure_count",
            "interval": "interval_seconds",
            "trip_duration": "trip_duration_seconds",
            "tripDuration": "trip_duration_seconds",
            "statusCodeRanges": "status_code_ranges",
            "errorReasons": "error_reasons",
            "acceptRetryAfter": "accept_retry_after",
        }
        for source, target in aliases.items():
            if target not in mapped and source in mapped:
                mapped[target] = mapped[source]
        if "interval_seconds" in mapped:
            mapped["interval_seconds"] = _duration_seconds(mapped["interval_seconds"])
        if "trip_duration_seconds" in mapped:
            mapped["trip_duration_seconds"] = _duration_seconds(mapped["trip_duration_seconds"])
        return mapped


class BackendConfig(BaseModel):
    url: str = ""
    description: str | None = None
    type: str = "single"  # single|pool
    pool: list[BackendPoolMemberConfig] = Field(default_factory=list)
    session_affinity: BackendSessionAffinityConfig | None = None
    circuit_breaker: BackendCircuitBreakerConfig | None = None
    auth_type: str = "none"  # none|basic|managed_identity|client_certificate
    basic_username: str | None = None
    basic_password: str | None = None
    managed_identity_resource: str | None = None
    authorization_scheme: str | None = None
    authorization_parameter: str | None = None
    header_credentials: dict[str, str] = Field(default_factory=dict)
    query_credentials: dict[str, str] = Field(default_factory=dict)
    client_certificate_thumbprints: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _map_arm_pool(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        mapped = dict(value)
        if "circuit_breaker" not in mapped and mapped.get("circuitBreaker") is not None:
            mapped["circuit_breaker"] = mapped["circuitBreaker"]
        pool = mapped.get("pool")
        if isinstance(pool, dict):
            if "session_affinity" not in mapped and pool.get("sessionAffinity") is not None:
                mapped["session_affinity"] = pool["sessionAffinity"]
            mapped["pool"] = pool.get("services") or pool.get("members") or []
        return mapped

    @model_validator(mode="after")
    def _validate_backend_shape(self) -> BackendConfig:
        backend_type = (self.type or "single").lower()
        if backend_type == "pool":
            if not self.pool:
                raise ValueError("pool backends require at least one pool member")
        elif not self.url:
            raise ValueError("url is required for non-pool backends")
        return self


@dataclass(frozen=True)
class RouteMatch:
    parameters: dict[str, str]
    precedence: tuple[int, ...]


_TEMPLATE_PARAMETER = re.compile(r"^\{(\*?[^{}]+)\}$")


def _normalize_path(path: str) -> str:
    value = path or "/"
    if not value.startswith("/"):
        value = "/" + value
    return value.rstrip("/") or "/"


def _path_segments(path: str) -> list[str]:
    normalized = _normalize_path(path)
    return [] if normalized == "/" else normalized.lstrip("/").split("/")


def _template_parts(url_template: str) -> tuple[str, list[tuple[str, str]]]:
    value = (url_template or "").strip()
    if not value.startswith("/"):
        value = "/" + value
    parsed = urlsplit(value)
    path = parsed.path or "/"
    return path, parse_qsl(parsed.query, keep_blank_values=True)


def _template_parameter(segment: str) -> tuple[str, bool] | None:
    if segment == "*":
        return "*", True
    match = _TEMPLATE_PARAMETER.fullmatch(segment)
    if match is None:
        return None
    value = match.group(1)
    wildcard = value.startswith("*")
    name = value[1:] if wildcard else value
    return (name, wildcard) if name else None


def _match_nonwildcard_segment(expected: str, actual: str) -> tuple[str | None, int] | None:
    parameter = _template_parameter(expected)
    if parameter:
        return (parameter[0], 2) if actual else None
    if expected.casefold() != actual.casefold():
        return None
    return None, 3


def _match_segments(template: list[str], request: list[str]) -> tuple[dict[str, str], tuple[int, ...]] | None:
    parameters: dict[str, str] = {}
    precedence: list[int] = []
    wildcard = False

    for index, expected in enumerate(template):
        parameter = _template_parameter(expected)
        if parameter and parameter[1]:
            if index != len(template) - 1:
                return None
            parameters[parameter[0]] = "/".join(request[index:])
            precedence.append(1)
            wildcard = True
            break
        if index >= len(request):
            return None
        actual = request[index]
        segment_match = _match_nonwildcard_segment(expected, actual)
        if segment_match is None:
            return None
        parameter_name, segment_precedence = segment_match
        if parameter_name is not None:
            parameters[parameter_name] = actual
        precedence.append(segment_precedence)

    if not wildcard and len(template) != len(request):
        return None
    return parameters, tuple(precedence)


def _query_items(query: Any) -> list[tuple[str, str]]:
    multi_items = getattr(query, "multi_items", None)
    if callable(multi_items):
        return [(str(key), str(value)) for key, value in multi_items()]
    if not isinstance(query, Mapping):
        return []
    return [(str(key), str(value)) for key, value in query.items()]


def _match_query_parts(template: list[tuple[str, str]], query: Any) -> tuple[dict[str, str], tuple[int, ...]] | None:
    values: dict[str, list[str]] = {}
    for key, value in _query_items(query):
        values.setdefault(key.casefold(), []).append(value)

    parameters: dict[str, str] = {}
    precedence: list[int] = []
    for name, expected in template:
        actual_values = values.get(name.casefold(), [])
        if not actual_values:
            return None
        parameter = _template_parameter(expected)
        actual = actual_values[0]
        if parameter:
            parameters[parameter[0]] = actual
            precedence.append(2)
        elif actual != expected:
            return None
        else:
            precedence.append(3)
    return parameters, tuple(precedence)


def _match_operation(api_path: str, url_template: str, path: str, query: Any) -> RouteMatch | None:
    template_path, query_template = _template_parts(url_template)
    template_segments = _path_segments(api_path) + _path_segments(template_path)
    path_match = _match_segments(template_segments, _path_segments(path))
    if path_match is None:
        return None
    query_match = _match_query_parts(query_template, query)
    if query_match is None:
        return None
    path_parameters, path_precedence = path_match
    query_parameters, query_precedence = query_match
    path_parameters.update(query_parameters)
    precedence = (
        len(template_segments),
        *path_precedence,
        len(query_template),
        *query_precedence,
    )
    return RouteMatch(parameters=path_parameters, precedence=precedence)


def _match_path_prefix(prefix: str, path: str) -> RouteMatch | None:
    prefix_segments = _path_segments(prefix)
    request_segments = _path_segments(path)
    if len(request_segments) < len(prefix_segments):
        return None
    if any(
        expected.casefold() != actual.casefold()
        for expected, actual in zip(prefix_segments, request_segments, strict=False)
    ):
        return None
    return RouteMatch(
        parameters={},
        precedence=(len(prefix_segments), *([3] * len(prefix_segments)), 0),
    )


def _remove_path_prefix(path: str, prefix: str) -> str:
    normalized_prefix = _normalize_path(prefix)
    incoming = path or "/"
    if normalized_prefix == "/":
        return incoming
    lowered_path = incoming.casefold()
    lowered_prefix = normalized_prefix.casefold()
    if lowered_path == lowered_prefix:
        return ""
    if lowered_path.startswith(lowered_prefix + "/"):
        return incoming[len(normalized_prefix) :]
    return incoming


class RouteConfig(BaseModel):
    name: str
    path_prefix: str
    host_match: list[str] = Field(default_factory=list)
    methods: list[str] | None = None
    api_id: str | None = None
    operation_id: str | None = None
    upstream_base_url: str
    upstream_path_prefix: str = ""
    backend: str | None = None
    product: str | None = None
    products: list[str] = Field(default_factory=list)
    api_version_set: str | None = None
    api_version: str | None = None
    api_protocols: list[str] | None = Field(default=None, exclude=True, repr=False)
    subscription_header_names: list[str] | None = None
    subscription_query_param_names: list[str] | None = None
    authz: RouteAuthzConfig | None = None
    policies_xml: str | None = None
    policies_xml_documents: list[str] = Field(default_factory=list)
    url_template: str | None = Field(default=None, exclude=True, repr=False)
    api_path_prefix: str | None = Field(default=None, exclude=True, repr=False)
    api_upstream_path_prefix: str | None = Field(default=None, exclude=True, repr=False)
    upstream_path_uses_operation_prefix: bool = Field(default=False, exclude=True, repr=False)

    def matches(self, *, method: str, path: str, query: Any = None) -> bool:
        return self.match(method=method, path=path, query=query) is not None

    def match(self, *, method: str, path: str, query: Any = None) -> RouteMatch | None:
        if self.url_template is not None:
            path_match = _match_operation(self.api_path_prefix or self.path_prefix, self.url_template, path, query)
        else:
            path_match = _match_path_prefix(self.path_prefix, path)
        if path_match is None:
            return None
        if self.methods and method.upper() not in {item.upper() for item in self.methods}:
            return None
        # APIM's public docs describe template forms but not tie-breaking. This
        # score follows observed APIM precedence: literals, then parameters,
        # then wildcards; declaration order is used only for exact ties.
        method_precedence = 1 if self.methods else 0
        return RouteMatch(path_match.parameters, (*path_match.precedence, method_precedence))

    def matches_path(self, path: str, query: Any = None) -> bool:
        return self._path_match(path, query=query) is not None

    def matches_api_path(self, path: str) -> bool:
        prefix = self.api_path_prefix or self.path_prefix
        return _match_path_prefix(prefix, path) is not None

    def _path_match(self, path: str, *, query: Any = None) -> RouteMatch | None:
        if self.url_template is not None:
            return _match_operation(self.api_path_prefix or self.path_prefix, self.url_template, path, query)
        return _match_path_prefix(self.path_prefix, path)

    def build_upstream_url(self, path: str, *, upstream_base_url: str | None = None) -> str:
        source_prefix = self.path_prefix
        if self.url_template is not None and not self.upstream_path_uses_operation_prefix:
            source_prefix = self.api_path_prefix or self.path_prefix
        remainder = _remove_path_prefix(path, source_prefix)
        if remainder and not remainder.startswith("/"):
            remainder = "/" + remainder
        upstream_prefix_value = self.upstream_path_prefix
        if self.url_template is not None and not self.upstream_path_uses_operation_prefix:
            if self.api_upstream_path_prefix is not None:
                upstream_prefix_value = self.api_upstream_path_prefix
        upstream_prefix = upstream_prefix_value.rstrip("/")
        upstream_path = (upstream_prefix + remainder) if upstream_prefix else remainder
        if not upstream_path:
            upstream_path = "/"
        base = (upstream_base_url or self.upstream_base_url).rstrip("/")
        return base + upstream_path


class GatewayConfig(BaseModel):
    schema_version: int = 1
    service: ServiceMetadataConfig = Field(default_factory=ServiceMetadataConfig)
    allowed_origins: list[str] = Field(default_factory=lambda: [http_url("localhost:3007")])
    allow_anonymous: bool = False
    client_certificate: ClientCertificateConfig = Field(default_factory=ClientCertificateConfig)
    oidc: OIDCConfig | None = None
    oidc_providers: dict[str, OIDCConfig] = Field(default_factory=dict)
    products: dict[str, ProductConfig] = Field(default_factory=dict)
    named_values: dict[str, NamedValueConfig] = Field(default_factory=dict)
    loggers: dict[str, LoggerConfig] = Field(default_factory=dict)
    diagnostics: dict[str, DiagnosticConfig] = Field(default_factory=dict)
    users: dict[str, UserConfig] = Field(default_factory=dict)
    groups: dict[str, GroupConfig] = Field(default_factory=dict)
    tags: dict[str, TagConfig] = Field(default_factory=dict)
    subscription: SubscriptionConfig = Field(default_factory=SubscriptionConfig)
    admin_token: str | None = None
    tenant_access: TenantAccessConfig = Field(default_factory=TenantAccessConfig)
    portal: PortalConfig = Field(default_factory=PortalConfig)
    proxy_timeout_seconds: float = 30.0
    proxy_max_attempts: int = 1
    proxy_retry_statuses: list[int] = Field(default_factory=lambda: [502, 503, 504])
    proxy_streaming: bool = True
    # These headers describe the simulator, not Azure API Management. They are
    # compatibility switches for demos that explicitly depend on them.
    inject_simulator_identity_headers: bool = False
    emit_simulator_response_headers: bool = False
    propagate_simulator_correlation_id: bool = False
    max_request_body_bytes: int = 1_048_576
    cache_enabled: bool = False
    cache_ttl_seconds: float = 5.0
    cache_max_entries: int = 1024
    trace_enabled: bool = False
    api_version_sets: dict[str, ApiVersionSetConfig] = Field(default_factory=dict)
    policy_fragments: dict[str, str] = Field(default_factory=dict)
    policies_xml: str | None = None
    policies_xml_documents: list[str] = Field(default_factory=list)
    backends: dict[str, BackendConfig] = Field(default_factory=dict)
    apis: dict[str, ApiConfig] = Field(default_factory=dict)
    routes: list[RouteConfig] = Field(default_factory=list)

    def materialize_routes(self) -> list[RouteConfig]:
        """Flatten the API catalogue into the flat route table the gateway matches.

        An API with operations becomes one route per operation, each inheriting
        from the API whatever it does not state for itself. An API with no
        operations has no gateway route.
        """
        if not self.apis:
            return list(self.routes)

        out: list[RouteConfig] = []
        for api_id, api in self.apis.items():
            api_base = ("/" + (api.path or "").strip("/")).rstrip("/") or "/"
            api_policy_docs = [api.policies_xml] if api.policies_xml else []

            if not api.operations:
                continue

            out.extend(
                _operation_route(api_id, api, operation_id, op, api_base, api_policy_docs)
                for operation_id, op in api.operations.items()
            )
        return out


def _api_policy_xml_entries(api_id: str, api: ApiConfig) -> list[tuple[str, str]]:
    """Policy XML values authored by one API and its operations."""
    entries: list[tuple[str, str]] = []
    if api.policies_xml:
        entries.append((f"API {api_id}", api.policies_xml))
    for operation_id, operation in api.operations.items():
        if operation.policies_xml:
            entries.append((f"operation {api_id}:{operation_id}", operation.policies_xml))
    return entries


def _route_policy_xml_entries(route: RouteConfig) -> list[tuple[str, str]]:
    """Policy XML values authored by one legacy route."""
    entries: list[tuple[str, str]] = []
    for index, xml in enumerate(route.policies_xml_documents):
        if xml:
            entries.append((f"route {route.name} document {index}", xml))
    if route.policies_xml:
        entries.append((f"route {route.name}", route.policies_xml))
    return entries


def _policy_xml_entries(cfg: GatewayConfig) -> list[tuple[str, str]]:
    """Policy XML values authored in the gateway document and its scopes."""
    entries: list[tuple[str, str]] = []
    for index, xml in enumerate(cfg.policies_xml_documents):
        if xml:
            entries.append((f"gateway document {index}", xml))
    if cfg.policies_xml:
        entries.append(("gateway", cfg.policies_xml))
    for product_id, product in cfg.products.items():
        if product.policies_xml:
            entries.append((f"product {product_id}", product.policies_xml))
    for api_id, api in cfg.apis.items():
        entries.extend(_api_policy_xml_entries(api_id, api))
    for route in cfg.routes:
        entries.extend(_route_policy_xml_entries(route))
    return entries


def validate_policy_config(cfg: GatewayConfig) -> GatewayConfig:
    """Reject malformed policy documents before the gateway can serve them."""
    from defusedxml import ElementTree

    from app.effective_policy import validate_policy_xml_syntax
    from app.named_values import validate_named_value_references

    for fragment_id, xml in cfg.policy_fragments.items():
        try:
            ElementTree.fromstring(f"<fragment>{xml}</fragment>")
        except ElementTree.ParseError as exc:
            raise ValueError(f"Invalid policy fragment XML at {fragment_id}") from exc
        try:
            validate_named_value_references(xml, cfg)
        except ValueError as exc:
            raise ValueError(f"Invalid policy fragment at {fragment_id}: {exc}") from exc

    for location, xml in _policy_xml_entries(cfg):
        try:
            validate_policy_xml_syntax(xml)
        except ValueError as exc:
            raise ValueError(f"Invalid policy XML at {location}: {exc}") from exc
        try:
            validate_named_value_references(xml, cfg)
        except ValueError as exc:
            raise ValueError(f"Invalid policy XML at {location}: {exc}") from exc
    return cfg


def _url_template_prefix(url_template: str) -> str:
    """The fixed leading path of an operation template."""
    path, _ = _template_parts(url_template)
    marker_positions = [position for position in (path.find("{"), path.find("*")) if position >= 0]
    if marker_positions:
        path = path[: min(marker_positions)]
    return path.rstrip("/")


def _operation_upstream_prefix(api: Any, op: Any, op_prefix: str) -> str:
    """Where the operation lands upstream.

    An operation that states its own prefix wins outright. Otherwise it keeps
    the legacy materialized prefix for projections; matching uses the API path
    and the request remainder to compose the actual upstream URL.
    """
    if op.upstream_path_prefix is not None:
        return op.upstream_path_prefix
    api_prefix = api.upstream_path_prefix.rstrip("/")
    if not op_prefix or op_prefix == "/":
        return api_prefix
    return f"{api_prefix}{op_prefix}" if api_prefix else op_prefix


def _operation_route(
    api_id: str, api: Any, operation_id: str, op: Any, api_base: str, api_policy_docs: list[str]
) -> RouteConfig:
    """One route for one operation, inheriting anything it does not state itself."""
    op_prefix = _url_template_prefix(op.url_template)
    full_prefix = api_base.rstrip("/")
    if op_prefix and op_prefix != "/":
        full_prefix += op_prefix
    full_prefix = full_prefix or "/"

    policies = [*api_policy_docs, op.policies_xml] if op.policies_xml else list(api_policy_docs)
    op_products = op.products if op.products is not None else api.products

    return RouteConfig(
        name=f"{api.name}:{op.name}",
        path_prefix=full_prefix,
        methods=[op.method],
        api_id=api_id,
        operation_id=operation_id,
        upstream_base_url=op.upstream_base_url or api.upstream_base_url,
        upstream_path_prefix=_operation_upstream_prefix(api, op, op_prefix),
        url_template=op.url_template,
        api_path_prefix=api_base,
        api_upstream_path_prefix=api.upstream_path_prefix,
        upstream_path_uses_operation_prefix=op.upstream_path_prefix is not None,
        api_protocols=list(api.protocols),
        backend=op.backend or api.backend,
        products=list(op_products or []),
        api_version_set=op.api_version_set or api.api_version_set,
        api_version=op.api_version or api.api_version,
        subscription_header_names=op.subscription_header_names or api.subscription_header_names,
        subscription_query_param_names=op.subscription_query_param_names or api.subscription_query_param_names,
        authz=op.authz,
        policies_xml_documents=policies,
    )


class OperationConfig(BaseModel):
    name: str
    method: str = "GET"
    url_template: str
    description: str | None = None
    upstream_base_url: str | None = None
    upstream_path_prefix: str | None = None
    backend: str | None = None
    products: list[str] | None = None
    api_version_set: str | None = None
    api_version: str | None = None
    subscription_header_names: list[str] | None = None
    subscription_query_param_names: list[str] | None = None
    authz: RouteAuthzConfig | None = None
    policies_xml: str | None = None
    tags: list[str] = Field(default_factory=list)
    template_parameters: list[OperationParameterConfig] = Field(default_factory=list)
    request: OperationRequestMetadataConfig | None = None
    responses: list[OperationResponseMetadataConfig] = Field(default_factory=list)


class ApiConfig(BaseModel):
    name: str
    path: str
    upstream_base_url: str
    upstream_path_prefix: str = ""
    backend: str | None = None
    # Learn documents the protocols property but not the omitted-field default;
    # preserve legacy simulator configs by exposing both schemes until set.
    protocols: list[str] = Field(default_factory=lambda: ["http", "https"])
    products: list[str] = Field(default_factory=list)
    api_version_set: str | None = None
    api_version: str | None = None
    revision: str | None = None
    revision_description: str | None = None
    version_description: str | None = None
    source_api_id: str | None = None
    is_current: bool | None = None
    is_online: bool | None = None
    subscription_header_names: list[str] | None = None
    subscription_query_param_names: list[str] | None = None
    policies_xml: str | None = None
    tags: list[str] = Field(default_factory=list)
    operations: dict[str, OperationConfig] = Field(default_factory=dict)
    schemas: dict[str, ApiSchemaConfig] = Field(default_factory=dict)
    revisions: dict[str, ApiRevisionConfig] = Field(default_factory=dict)
    releases: dict[str, ApiReleaseConfig] = Field(default_factory=dict)


def _default_config_from_env() -> GatewayConfig:
    backend_base_url = os.getenv("BACKEND_BASE_URL", http_url("mock-backend:8080"))
    backend_path_prefix = os.getenv("BACKEND_PATH_PREFIX", "/api")
    oidc_issuer = os.getenv("OIDC_ISSUER", "").strip()
    oidc_audience = os.getenv("OIDC_AUDIENCE", "").strip()
    oidc_jwks_uri = os.getenv("OIDC_JWKS_URI", "").strip()
    default_allowed_origins = ",".join([http_url("localhost:3000"), http_url("localhost:8000")])
    allowed_origins = [
        origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", default_allowed_origins).split(",") if origin.strip()
    ]
    allow_anonymous = os.getenv("ALLOW_ANONYMOUS", "true").lower() == "true"
    oidc_values = {
        "OIDC_ISSUER": oidc_issuer,
        "OIDC_AUDIENCE": oidc_audience,
        "OIDC_JWKS_URI": oidc_jwks_uri,
    }
    missing_oidc = [name for name, value in oidc_values.items() if not value]
    if len(missing_oidc) != len(oidc_values) and missing_oidc:
        raise ValueError(f"incomplete OIDC configuration; missing {', '.join(missing_oidc)}")
    if not allow_anonymous and missing_oidc:
        raise ValueError("OIDC_ISSUER, OIDC_AUDIENCE, and OIDC_JWKS_URI are required when ALLOW_ANONYMOUS=false")
    oidc_config = (
        OIDCConfig(issuer=oidc_issuer, audience=oidc_audience, jwks_uri=oidc_jwks_uri) if not missing_oidc else None
    )

    subscription_key = os.getenv("APIM_SUBSCRIPTION_KEY", "")
    keys: dict[str, SubscriptionIdentity] = {}
    subscriptions: dict[str, Subscription] = {}
    if subscription_key:
        default_subscription = Subscription(
            id="sub-default",
            name="default",
            keys=SubscriptionKeyPair(
                primary=subscription_key,
                secondary=f"{subscription_key}-secondary",
            ),
            products=["default"],
        )
        subscriptions[default_subscription.id] = default_subscription
        keys[subscription_key] = default_subscription.identity()

    admin_token = os.getenv("APIM_ADMIN_TOKEN", "").strip() or None

    tenant_primary = os.getenv("APIM_TENANT_ACCESS_PRIMARY_KEY", "").strip() or None
    tenant_secondary = os.getenv("APIM_TENANT_ACCESS_SECONDARY_KEY", "").strip() or None
    tenant_enabled = bool(tenant_primary or tenant_secondary)

    return GatewayConfig(
        allowed_origins=allowed_origins or ["*"],
        allow_anonymous=allow_anonymous,
        oidc=oidc_config,
        products={"default": ProductConfig(name="Default", require_subscription=bool(subscription_key))},
        subscription=SubscriptionConfig(required=bool(subscription_key), keys=keys, subscriptions=subscriptions),
        admin_token=admin_token,
        tenant_access=TenantAccessConfig(
            enabled=tenant_enabled,
            primary_key=tenant_primary,
            secondary_key=tenant_secondary,
        ),
        routes=[
            RouteConfig(
                name="default",
                path_prefix="/api",
                upstream_base_url=backend_base_url,
                upstream_path_prefix=backend_path_prefix,
                product="default",
            )
        ],
    )


def load_config() -> GatewayConfig:
    config_path = os.getenv("APIM_CONFIG_PATH", "").strip()
    if not config_path:
        return _default_config_from_env()
    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)
    return validate_policy_config(GatewayConfig.model_validate(data))
