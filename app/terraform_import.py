from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from app.config import (
    ApiConfig,
    ApiReleaseConfig,
    ApiRevisionConfig,
    ApiSchemaConfig,
    ApiVersioningScheme,
    ApiVersionSetConfig,
    BackendCircuitBreakerConfig,
    BackendConfig,
    BackendPoolMemberConfig,
    ClientCertificateMode,
    DiagnosticConfig,
    DiagnosticDataMaskingConfig,
    DiagnosticHttpMessageConfig,
    DiagnosticMaskingRuleConfig,
    GatewayConfig,
    GroupConfig,
    KeyVaultNamedValueConfig,
    LoggerApplicationInsightsConfig,
    LoggerConfig,
    LoggerEventHubConfig,
    NamedValueConfig,
    OperationConfig,
    OperationExampleConfig,
    OperationParameterConfig,
    OperationRepresentationConfig,
    OperationRequestMetadataConfig,
    OperationResponseMetadataConfig,
    ProductConfig,
    ProductState,
    ServiceHostnameConfiguration,
    ServiceMetadataConfig,
    Subscription,
    SubscriptionKeyPair,
    SubscriptionScope,
    SubscriptionState,
    TagConfig,
    UserConfig,
    validate_policy_config,
)
from app.openapi_import import parse_api_import
from app.urls import http_url


@dataclass(frozen=True)
class TFResource:
    address: str
    type: str
    name: str
    values: dict[str, Any]


@dataclass(frozen=True)
class ImportDiagnostic:
    status: str
    scope: str
    feature: str
    detail: str


@dataclass(frozen=True)
class ImportResult:
    config: GatewayConfig
    diagnostics: list[ImportDiagnostic] = field(default_factory=list)
    service_imported: bool = False


def _iter_module_resources(module: dict[str, Any]) -> Iterable[TFResource]:
    for res in module.get("resources") or []:
        if not isinstance(res, dict):
            continue
        address = str(res.get("address") or "")
        rtype = str(res.get("type") or "")
        name = str(res.get("name") or "")
        values = res.get("values")
        if isinstance(values, dict):
            yield TFResource(address=address, type=rtype, name=name, values=values)

    for child in module.get("child_modules") or []:
        if isinstance(child, dict):
            yield from _iter_module_resources(child)


def _iter_resources(tf: dict[str, Any]) -> list[TFResource]:
    values = tf.get("values")
    if not isinstance(values, dict):
        planned = tf.get("planned_values")
        values = planned if isinstance(planned, dict) else {}

    root = values.get("root_module")
    if not isinstance(root, dict):
        return []
    return list(_iter_module_resources(root))


def iter_tofu_resources(tf: dict[str, Any]) -> list[TFResource]:
    return _iter_resources(tf)


def _first_block(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return None


def _string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key, item in value.items():
        if isinstance(item, list):
            out[str(key)] = ",".join(str(part) for part in item)
        elif item is not None:
            out[str(key)] = str(item)
    return out


def _resource_name_from_id(resource_id: str, id_to_name: dict[str, str]) -> str | None:
    if not resource_id:
        return None
    if resource_id in id_to_name:
        return id_to_name[resource_id]
    tail = resource_id.rstrip("/").split("/")[-1]
    return id_to_name.get(tail) or tail or None


def _arm_id_segment(resource_id: str, marker: str) -> str | None:
    if not resource_id:
        return None
    parts = resource_id.strip("/").split("/")
    if marker not in parts:
        return None
    idx = parts.index(marker)
    if idx + 1 >= len(parts):
        return None
    return parts[idx + 1] or None


def _api_name_and_revision_from_resource_id(resource_id: str) -> tuple[str | None, str | None]:
    tail = _arm_id_segment(resource_id, "apis")
    if not tail:
        return None, None
    if ";rev=" in tail:
        api_name, revision = tail.split(";rev=", 1)
        return api_name or None, revision or None
    return tail, None


def _api_import_block(values: dict[str, Any]) -> dict[str, Any] | None:
    return _first_block(values.get("import"))


def _subscription_key_parameter_names(values: dict[str, Any]) -> tuple[list[str] | None, list[str] | None]:
    block = _first_block(values.get("subscription_key_parameter_names"))
    if block is None:
        return None, None
    header = str(block.get("header") or "").strip() or None
    query = str(block.get("query") or "").strip() or None
    return ([header] if header else None, [query] if query else None)


def _backend_pool_members(values: dict[str, Any]) -> list[BackendPoolMemberConfig]:
    pool = values.get("pool")
    if isinstance(pool, dict):
        raw_members = pool.get("services") or pool.get("members") or []
    elif isinstance(pool, list):
        raw_members = pool
    else:
        raw_members = values.get("pool_services") or []
    members: list[BackendPoolMemberConfig] = []
    if not isinstance(raw_members, list):
        return members
    for item in raw_members:
        if not isinstance(item, dict):
            continue
        backend_id = item.get("backend_id") or item.get("name") or item.get("id")
        if not backend_id:
            continue
        backend_id = str(backend_id).rstrip("/").rsplit("/", 1)[-1]
        members.append(
            BackendPoolMemberConfig(
                backend_id=backend_id,
                weight=int(item.get("weight") or 1),
                priority=int(item.get("priority") or 1),
            )
        )
    return members


def _first_present(block: dict[str, Any], *names: str) -> Any:
    """The first of these keys the block actually carries.

    Terraform, AzAPI and ARM each spell these fields differently, and a document
    may use any one of them.
    """
    for name in names:
        value = block.get(name)
        if value:
            return value
    return None


def _status_code_list(value: Any) -> list[int]:
    """Status codes from a list that may hold ints or numeric strings."""
    if not isinstance(value, list):
        return []
    codes: list[int] = []
    for item in value:
        if isinstance(item, int):
            codes.append(item)
        elif isinstance(item, str) and item.isdigit():
            codes.append(int(item))
    return codes


def _backend_circuit_breaker(values: dict[str, Any]) -> BackendCircuitBreakerConfig | None:
    """Read a backend circuit breaker, in whichever spelling the source used.

    Returns None when nothing recognisable is declared, so the caller falls back
    to the pool's breaker rather than to a breaker built from defaults.
    """
    raw = values.get("circuit_breaker") or values.get("circuitBreaker")
    block = _first_block(raw) if not isinstance(raw, dict) else raw
    if not block:
        return None

    rules = block.get("rules")
    if isinstance(rules, list) and rules and isinstance(rules[0], dict):
        block = rules[0]

    kwargs: dict[str, Any] = {}
    failure_count = _first_present(block, "failure_count", "failureCount", "trip_threshold")
    if failure_count is not None:
        kwargs["failure_count"] = int(failure_count)
    interval = _first_present(block, "interval_seconds", "intervalSeconds", "interval")
    if interval is not None:
        kwargs["interval_seconds"] = float(interval)
    trip = _first_present(block, "trip_duration_seconds", "tripDurationSeconds", "trip_duration")
    if trip is not None:
        kwargs["trip_duration_seconds"] = float(trip)
    statuses = _status_code_list(_first_present(block, "error_statuses", "errorStatuses", "status_code_ranges"))
    if statuses:
        kwargs["error_statuses"] = statuses

    return BackendCircuitBreakerConfig(**kwargs) if kwargs else None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _examples(value: Any) -> list[OperationExampleConfig]:
    if not isinstance(value, list):
        return []
    out: list[OperationExampleConfig] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        out.append(
            OperationExampleConfig(
                name=name,
                summary=str(item.get("summary")) if item.get("summary") else None,
                description=str(item.get("description")) if item.get("description") else None,
                value=item.get("value"),
                external_value=str(item.get("external_value")) if item.get("external_value") else None,
            )
        )
    return out


def _parameter_blocks(value: Any) -> list[OperationParameterConfig]:
    if not isinstance(value, list):
        return []
    out: list[OperationParameterConfig] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        param_type = str(item.get("type") or "").strip()
        if not name or not param_type:
            continue
        out.append(
            OperationParameterConfig(
                name=name,
                required=bool(item.get("required")),
                type=param_type,
                description=str(item.get("description")) if item.get("description") else None,
                default_value=str(item.get("default_value")) if item.get("default_value") is not None else None,
                values=_string_list(item.get("values")),
                examples=_examples(item.get("example")),
                schema_id=str(item.get("schema_id")) if item.get("schema_id") else None,
                type_name=str(item.get("type_name")) if item.get("type_name") else None,
            )
        )
    return out


def _representation_blocks(value: Any) -> list[OperationRepresentationConfig]:
    if not isinstance(value, list):
        return []
    out: list[OperationRepresentationConfig] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        content_type = str(item.get("content_type") or "").strip()
        if not content_type:
            continue
        out.append(
            OperationRepresentationConfig(
                content_type=content_type,
                form_parameters=_parameter_blocks(item.get("form_parameter")),
                examples=_examples(item.get("example")),
                schema_id=str(item.get("schema_id")) if item.get("schema_id") else None,
                type_name=str(item.get("type_name")) if item.get("type_name") else None,
            )
        )
    return out


def _request_metadata(value: Any) -> OperationRequestMetadataConfig | None:
    block = _first_block(value)
    if block is None:
        return None
    request = OperationRequestMetadataConfig(
        description=str(block.get("description")) if block.get("description") else None,
        headers=_parameter_blocks(block.get("header")),
        query_parameters=_parameter_blocks(block.get("query_parameter")),
        representations=_representation_blocks(block.get("representation")),
    )
    if request == OperationRequestMetadataConfig():
        return None
    return request


def _response_metadata(value: Any) -> list[OperationResponseMetadataConfig]:
    if not isinstance(value, list):
        return []
    out: list[OperationResponseMetadataConfig] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        status_code_raw = item.get("status_code")
        if status_code_raw is None:
            continue
        try:
            status_code = int(status_code_raw)
        except (TypeError, ValueError):
            continue
        out.append(
            OperationResponseMetadataConfig(
                status_code=status_code,
                description=str(item.get("description")) if item.get("description") else None,
                headers=_parameter_blocks(item.get("header")),
                representations=_representation_blocks(item.get("representation")),
            )
        )
    return out


def _api_schema(values: dict[str, Any]) -> ApiSchemaConfig:
    definitions = values.get("definitions")
    if not isinstance(definitions, dict):
        definitions = {}
    components = values.get("components")
    if not isinstance(components, dict):
        components = {}
    raw_value = values.get("value")
    value = str(raw_value) if raw_value is not None else None
    return ApiSchemaConfig(
        content_type=str(values.get("content_type") or "application/json"),
        value=value,
        definitions=definitions,
        components=components,
    )


def _diagnostic_masking_rules(value: Any) -> list[DiagnosticMaskingRuleConfig]:
    if not isinstance(value, list):
        return []
    out: list[DiagnosticMaskingRuleConfig] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        mode = str(item.get("mode") or "").strip()
        masked_value = str(item.get("value") or "").strip()
        if not mode or not masked_value:
            continue
        out.append(DiagnosticMaskingRuleConfig(mode=mode, value=masked_value))
    return out


def _diagnostic_data_masking(value: Any) -> DiagnosticDataMaskingConfig | None:
    block = _first_block(value)
    if block is None:
        return None
    data_masking = DiagnosticDataMaskingConfig(
        query_params=_diagnostic_masking_rules(block.get("query_params")),
        headers=_diagnostic_masking_rules(block.get("headers")),
    )
    if data_masking == DiagnosticDataMaskingConfig():
        return None
    return data_masking


def _diagnostic_http_message(value: Any) -> DiagnosticHttpMessageConfig | None:
    block = _first_block(value)
    if block is None:
        return None
    raw_body_bytes = block.get("body_bytes")
    body_bytes: int | None = None
    if raw_body_bytes is not None:
        try:
            body_bytes = int(raw_body_bytes)
        except (TypeError, ValueError):
            body_bytes = None
    payload = DiagnosticHttpMessageConfig(
        body_bytes=body_bytes,
        headers_to_log=_string_list(block.get("headers_to_log")),
        data_masking=_diagnostic_data_masking(block.get("data_masking")),
    )
    if payload == DiagnosticHttpMessageConfig():
        return None
    return payload


def _ensure_tag(
    tags: dict[str, TagConfig],
    *,
    tag_name: str,
    display_name: str | None = None,
) -> bool:
    existing = tags.get(tag_name)
    if existing is not None:
        if display_name and existing.display_name == tag_name:
            existing.display_name = display_name
        return False
    tags[tag_name] = TagConfig(display_name=display_name or tag_name)
    return True


def _ensure_group(groups: dict[str, GroupConfig], *, group_name: str) -> bool:
    if group_name in groups:
        return False
    groups[group_name] = GroupConfig(id=group_name, name=group_name)
    return True


def _ensure_user(users: dict[str, UserConfig], *, user_id: str) -> bool:
    if user_id in users:
        return False
    users[user_id] = UserConfig(id=user_id, name=user_id)
    return True


AZAPI_PROVIDER_RESOURCE_TYPES = {"azapi_resource", "azapi_update_resource"}

AZAPI_APIM_CHILD_EQUIVALENTS = {
    "Microsoft.ApiManagement/service/apis": "azurerm_api_management_api",
    "Microsoft.ApiManagement/service/apis/operations": "azurerm_api_management_api_operation",
    "Microsoft.ApiManagement/service/apis/schemas": "azurerm_api_management_api_schema",
    "Microsoft.ApiManagement/service/apis/policies": "azurerm_api_management_api_policy",
    "Microsoft.ApiManagement/service/apis/operations/policies": "azurerm_api_management_api_operation_policy",
    "Microsoft.ApiManagement/service/products": "azurerm_api_management_product",
    "Microsoft.ApiManagement/service/subscriptions": "azurerm_api_management_subscription",
    "Microsoft.ApiManagement/service/backends": "azurerm_api_management_backend",
    "Microsoft.ApiManagement/service/namedValues": "azurerm_api_management_named_value",
    "Microsoft.ApiManagement/service/loggers": "azurerm_api_management_logger",
    "Microsoft.ApiManagement/service/diagnostics": "azurerm_api_management_diagnostic",
    "Microsoft.ApiManagement/service/apiVersionSets": "azurerm_api_management_api_version_set",
    "Microsoft.ApiManagement/service/policies": "azurerm_api_management_policy",
}


def arm_resource_type(resource: TFResource) -> str | None:
    if resource.type not in AZAPI_PROVIDER_RESOURCE_TYPES:
        return None
    raw = resource.values.get("type")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return raw.split("@", 1)[0]


def azapi_body(values: dict[str, Any]) -> dict[str, Any]:
    raw = values.get("body")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "enabled", "yes"}:
            return True
        if lowered in {"false", "disabled", "no"}:
            return False
    return None


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _service_scope(name: str) -> str:
    return f"service:{name}"


def _service_hostnames_from_azurerm(values: dict[str, Any]) -> list[ServiceHostnameConfiguration]:
    block = _first_block(values.get("hostname_configuration"))
    if block is None:
        return []

    out: list[ServiceHostnameConfiguration] = []
    type_map = {
        "management": "Management",
        "portal": "Portal",
        "developer_portal": "DeveloperPortal",
        "proxy": "Proxy",
        "scm": "Scm",
    }
    for tf_type, apim_type in type_map.items():
        for item in block.get(tf_type) or []:
            if not isinstance(item, dict):
                continue
            host_name = str(item.get("host_name") or "").strip()
            if not host_name:
                continue
            out.append(
                ServiceHostnameConfiguration(
                    type=apim_type,
                    host_name=host_name,
                    negotiate_client_certificate=bool(item.get("negotiate_client_certificate")),
                    default_ssl_binding=bool(item.get("default_ssl_binding")),
                )
            )
    return out


def _service_hostnames_from_azapi(body: dict[str, Any]) -> list[ServiceHostnameConfiguration]:
    properties = body.get("properties")
    if not isinstance(properties, dict):
        return []

    raw_hostnames = properties.get("hostnameConfigurations") or properties.get("hostname_configurations")
    if not isinstance(raw_hostnames, list):
        return []

    out: list[ServiceHostnameConfiguration] = []
    for item in raw_hostnames:
        if not isinstance(item, dict):
            continue
        host_name = str(item.get("hostName") or item.get("host_name") or "").strip()
        host_type = str(item.get("type") or "").strip()
        if not host_name or not host_type:
            continue
        out.append(
            ServiceHostnameConfiguration(
                type=host_type,
                host_name=host_name,
                negotiate_client_certificate=bool(
                    _coerce_bool(item.get("negotiateClientCertificate", item.get("negotiate_client_certificate")))
                ),
                default_ssl_binding=bool(_coerce_bool(item.get("defaultSslBinding", item.get("default_ssl_binding")))),
            )
        )
    return out


def _service_mode_from_flags(
    *, client_certificate_enabled: bool | None, hostnames: list[ServiceHostnameConfiguration]
) -> ClientCertificateMode | None:
    if client_certificate_enabled:
        return ClientCertificateMode.Required
    if any(item.negotiate_client_certificate for item in hostnames):
        return ClientCertificateMode.Optional
    return None


def _import_azurerm_service(
    values: dict[str, Any], fallback_name: str
) -> tuple[ServiceMetadataConfig, ClientCertificateMode | None]:
    name = str(values.get("name") or fallback_name or "apim-simulator")
    hostnames = _service_hostnames_from_azurerm(values)
    service = ServiceMetadataConfig(
        name=name,
        display_name=name,
        public_network_access_enabled=_coerce_bool(values.get("public_network_access_enabled")),
        virtual_network_type=(str(values.get("virtual_network_type")) if values.get("virtual_network_type") else None),
        hostname_configurations=hostnames,
    )
    mode = _service_mode_from_flags(
        client_certificate_enabled=_coerce_bool(values.get("client_certificate_enabled")),
        hostnames=hostnames,
    )
    return service, mode


def _import_azapi_service(
    values: dict[str, Any], fallback_name: str
) -> tuple[ServiceMetadataConfig, ClientCertificateMode | None, list[ImportDiagnostic]]:
    body = azapi_body(values)
    properties = body.get("properties") if isinstance(body.get("properties"), dict) else {}
    name = str(values.get("name") or fallback_name or "apim-simulator")
    hostnames = _service_hostnames_from_azapi(body)
    diagnostics: list[ImportDiagnostic] = []
    scope = _service_scope(name)

    public_network_access = properties.get("publicNetworkAccess", properties.get("public_network_access"))
    virtual_network_type = properties.get("virtualNetworkType", properties.get("virtual_network_type"))
    client_certificate_enabled = properties.get("enableClientCertificate", properties.get("enable_client_certificate"))

    if public_network_access is not None:
        diagnostics.append(
            ImportDiagnostic(
                status="adapted",
                scope=scope,
                feature="properties.publicNetworkAccess",
                detail="Imported into local service metadata only; Azure control-plane reachability is not enforced locally.",
            )
        )
    if virtual_network_type is not None:
        diagnostics.append(
            ImportDiagnostic(
                status="adapted",
                scope=scope,
                feature="properties.virtualNetworkType",
                detail="Imported into local service metadata only; Azure VNet placement is not enforced locally.",
            )
        )
    if hostnames:
        diagnostics.append(
            ImportDiagnostic(
                status="adapted",
                scope=scope,
                feature="properties.hostnameConfigurations",
                detail="Imported as descriptive host metadata; TLS termination and custom-domain ownership remain external.",
            )
        )
    if client_certificate_enabled is not None:
        diagnostics.append(
            ImportDiagnostic(
                status="adapted",
                scope=scope,
                feature="properties.enableClientCertificate",
                detail="Mapped onto the simulator's existing client-certificate mode.",
            )
        )

    allowed_top_level = {"properties"}
    allowed_properties = {
        "publicNetworkAccess",
        "public_network_access",
        "virtualNetworkType",
        "virtual_network_type",
        "hostnameConfigurations",
        "hostname_configurations",
        "enableClientCertificate",
        "enable_client_certificate",
    }
    for key in sorted(key for key in body if key not in allowed_top_level):
        diagnostics.append(
            ImportDiagnostic(
                status="unsupported",
                scope=scope,
                feature=key,
                detail="This AzAPI APIM service field is not imported into the simulator.",
            )
        )
    for key in sorted(key for key in properties if key not in allowed_properties):
        diagnostics.append(
            ImportDiagnostic(
                status="unsupported",
                scope=scope,
                feature=f"properties.{key}",
                detail="This AzAPI APIM service property is not imported into the simulator.",
            )
        )

    service = ServiceMetadataConfig(
        name=name,
        display_name=name,
        public_network_access_enabled=_coerce_bool(public_network_access),
        virtual_network_type=str(virtual_network_type) if virtual_network_type is not None else None,
        hostname_configurations=hostnames,
    )
    mode = _service_mode_from_flags(
        client_certificate_enabled=_coerce_bool(client_certificate_enabled),
        hostnames=hostnames,
    )
    return service, mode, diagnostics


@dataclass
class _ImportAccumulator:
    """What the passes build up as they walk the OpenTofu resource list.

    The importer makes three passes because APIM resources reference each other:
    an `azurerm_api_management_product_group` names a product and a group that
    only exist once the first pass has read them. Holding the half-built tenant
    document in one object is what lets each resource type be a small function
    instead of another branch in an 800-line loop.
    """

    fetcher: Callable[[str], str] | None = None
    service: ServiceMetadataConfig = field(default_factory=ServiceMetadataConfig)
    service_imported: bool = False
    client_certificate_mode: ClientCertificateMode | None = None
    gateway_policy: str | None = None
    products: dict[str, ProductConfig] = field(default_factory=dict)
    subscriptions: dict[str, Subscription] = field(default_factory=dict)
    named_values: dict[str, NamedValueConfig] = field(default_factory=dict)
    loggers: dict[str, LoggerConfig] = field(default_factory=dict)
    diagnostic_resources: dict[str, DiagnosticConfig] = field(default_factory=dict)
    users: dict[str, UserConfig] = field(default_factory=dict)
    groups: dict[str, GroupConfig] = field(default_factory=dict)
    api_version_sets: dict[str, ApiVersionSetConfig] = field(default_factory=dict)
    backends: dict[str, BackendConfig] = field(default_factory=dict)
    apis: dict[str, ApiConfig] = field(default_factory=dict)
    tags: dict[str, TagConfig] = field(default_factory=dict)
    id_to_name: dict[str, str] = field(default_factory=dict)
    diagnostics: list[ImportDiagnostic] = field(default_factory=list)


def _import_service(res: TFResource, acc: _ImportAccumulator) -> None:
    acc.service, mode = _import_azurerm_service(res.values, res.name)
    acc.service_imported = True
    if mode is not None:
        acc.client_certificate_mode = mode


def _import_product(res: TFResource, acc: _ImportAccumulator) -> None:
    product_id = str(res.values.get("product_id") or res.name)
    display_name = str(res.values.get("display_name") or product_id)
    subscription_required = res.values.get("subscription_required")
    require_subscription = bool(subscription_required) if subscription_required is not None else True
    published = res.values.get("published")
    state = ProductState.Published if (published is None or bool(published)) else ProductState.NotPublished
    approval_required = bool(res.values.get("approval_required"))
    if approval_required and not require_subscription:
        acc.diagnostics.append(
            ImportDiagnostic(
                status="adapted",
                scope=f"product:{product_id}",
                feature="approval_required",
                detail="approval_required needs subscription_required; imported without approval.",
            )
        )
        approval_required = False
    acc.products[product_id] = ProductConfig(
        name=display_name,
        state=state,
        require_subscription=require_subscription,
        approval_required=approval_required,
    )


def _import_group(res: TFResource, acc: _ImportAccumulator) -> None:
    group_name = str(res.values.get("name") or res.name).strip()
    if not group_name:
        return
    acc.groups[group_name] = GroupConfig(
        id=group_name,
        name=str(res.values.get("display_name") or group_name),
        description=str(res.values.get("description")) if res.values.get("description") else None,
        external_id=str(res.values.get("external_id")) if res.values.get("external_id") else None,
        type=str(res.values.get("type") or "custom"),
    )
    acc.diagnostics.append(
        ImportDiagnostic(
            status="supported",
            scope=f"group:{group_name}",
            feature="group",
            detail="Imported API Management group.",
        )
    )


def _import_user(res: TFResource, acc: _ImportAccumulator) -> None:
    user_id = str(res.values.get("user_id") or res.name).strip()
    if not user_id:
        return
    first_name = str(res.values.get("first_name") or "").strip() or None
    last_name = str(res.values.get("last_name") or "").strip() or None
    full_name = " ".join(part for part in [first_name, last_name] if part).strip() or user_id
    acc.users[user_id] = UserConfig(
        id=user_id,
        email=str(res.values.get("email")) if res.values.get("email") else None,
        name=full_name,
        first_name=first_name,
        last_name=last_name,
        note=str(res.values.get("note")) if res.values.get("note") else None,
        state=str(res.values.get("state")) if res.values.get("state") else None,
        confirmation=str(res.values.get("confirmation")) if res.values.get("confirmation") else None,
    )
    acc.diagnostics.append(
        ImportDiagnostic(
            status="supported",
            scope=f"user:{user_id}",
            feature="user",
            detail="Imported API Management user.",
        )
    )
    if res.values.get("password") is not None:
        acc.diagnostics.append(
            ImportDiagnostic(
                status="adapted",
                scope=f"user:{user_id}",
                feature="password",
                detail="User passwords are not stored or enforced by the simulator.",
            )
        )


def _import_tag(res: TFResource, acc: _ImportAccumulator) -> None:
    tag_name = str(res.values.get("name") or res.name).strip()
    if not tag_name:
        return
    _ensure_tag(
        acc.tags,
        tag_name=tag_name,
        display_name=str(res.values.get("display_name") or tag_name),
    )
    acc.diagnostics.append(
        ImportDiagnostic(
            status="supported",
            scope=f"tag:{tag_name}",
            feature="tag",
            detail="Imported API Management tag.",
        )
    )


def _import_subscription(res: TFResource, acc: _ImportAccumulator) -> None:
    sub_id = str(res.values.get("subscription_id") or res.values.get("name") or res.name)
    display_name = str(res.values.get("display_name") or res.values.get("name") or sub_id)
    primary = str(res.values.get("primary_key") or "")
    secondary = str(res.values.get("secondary_key") or "")
    raw_api_id = str(res.values.get("api_id") or "").strip()
    raw_product_id = str(res.values.get("product_id") or "").strip()
    api_id = _arm_id_segment(raw_api_id, "apis") if raw_api_id else None
    if api_id and ";rev=" in api_id:
        api_id = api_id.split(";rev=", 1)[0] or None
    product_id = _arm_id_segment(raw_product_id, "products") if raw_product_id else None
    if raw_product_id and product_id is None:
        product_id = raw_product_id
    # The AzureRM resource uses optional api_id/product_id; neither means
    # all-APIs in the APIM subscription model described by Learn.
    all_apis = bool(_coerce_bool(res.values.get("all_apis"))) or not raw_api_id and not raw_product_id
    service_scoped = bool(_coerce_bool(res.values.get("service_scoped")))
    scope = res.values.get("scope")
    raw_state = str(res.values.get("state") or "").strip().lower()
    try:
        sub_state = SubscriptionState(raw_state) if raw_state else SubscriptionState.Active
    except ValueError:
        acc.diagnostics.append(
            ImportDiagnostic(
                status="adapted",
                scope=f"subscription:{sub_id}",
                feature="state",
                detail=f"Unknown subscription state {raw_state!r}; imported as active.",
            )
        )
        sub_state = SubscriptionState.Active
    try:
        acc.subscriptions[sub_id] = Subscription(
            id=sub_id,
            name=display_name,
            keys=SubscriptionKeyPair(primary=primary, secondary=secondary),
            state=sub_state,
            products=[product_id] if product_id else [],
            scope=scope,
            api_id=api_id,
            all_apis=all_apis,
            service_scoped=service_scoped,
        )
    except ValueError as exc:
        raise ValueError(f"Invalid subscription {sub_id!r}: {exc}") from exc


def _import_named_value(res: TFResource, acc: _ImportAccumulator) -> None:
    name = str(res.values.get("display_name") or res.values.get("name") or res.name)
    secret = bool(res.values.get("secret"))
    key_vault_block = _first_block(res.values.get("value_from_key_vault"))
    value_from_key_vault = None
    if key_vault_block is not None and key_vault_block.get("secret_id"):
        value_from_key_vault = KeyVaultNamedValueConfig(
            secret_id=str(key_vault_block["secret_id"]),
            identity_client_id=(
                str(key_vault_block.get("identity_client_id")) if key_vault_block.get("identity_client_id") else None
            ),
        )
        acc.diagnostics.append(
            ImportDiagnostic(
                status="adapted",
                scope=f"named-value:{name}",
                feature="value_from_key_vault",
                detail="Key Vault-backed named values require APIM_NAMED_VALUE_<NAME> env overrides locally.",
            )
        )
    value = res.values.get("value")
    acc.named_values[name] = NamedValueConfig(
        value=str(value) if value is not None else None,
        secret=secret,
        value_from_key_vault=value_from_key_vault,
    )


def _import_logger(res: TFResource, acc: _ImportAccumulator) -> None:
    logger_id = str(res.values.get("name") or res.name).strip()
    if not logger_id:
        return
    app_insights_block = _first_block(res.values.get("application_insights"))
    eventhub_block = _first_block(res.values.get("eventhub"))
    logger_type = "application_insights" if app_insights_block else "eventhub" if eventhub_block else "custom"
    acc.loggers[logger_id] = LoggerConfig(
        logger_type=logger_type,
        description=str(res.values.get("description")) if res.values.get("description") else None,
        buffered=bool(res.values.get("buffered", True)),
        resource_id=str(res.values.get("resource_id")) if res.values.get("resource_id") else None,
        application_insights=(
            LoggerApplicationInsightsConfig(
                connection_string=(
                    str(app_insights_block.get("connection_string"))
                    if app_insights_block.get("connection_string")
                    else None
                ),
                instrumentation_key=(
                    str(app_insights_block.get("instrumentation_key"))
                    if app_insights_block.get("instrumentation_key")
                    else None
                ),
            )
            if app_insights_block is not None
            else None
        ),
        eventhub=(
            LoggerEventHubConfig(
                name=str(eventhub_block.get("name") or logger_id),
                connection_string=(
                    str(eventhub_block.get("connection_string")) if eventhub_block.get("connection_string") else None
                ),
                endpoint_uri=str(eventhub_block.get("endpoint_uri")) if eventhub_block.get("endpoint_uri") else None,
                user_assigned_identity_client_id=(
                    str(eventhub_block.get("user_assigned_identity_client_id"))
                    if eventhub_block.get("user_assigned_identity_client_id")
                    else None
                ),
            )
            if eventhub_block is not None
            else None
        ),
    )
    acc.diagnostics.append(
        ImportDiagnostic(
            status="supported",
            scope=f"logger:{logger_id}",
            feature="logger",
            detail="Imported API Management logger for read-only local inspection.",
        )
    )
    if app_insights_block is not None:
        acc.diagnostics.append(
            ImportDiagnostic(
                status="adapted",
                scope=f"logger:{logger_id}",
                feature="application_insights",
                detail=(
                    "Logger Application Insights settings are descriptive only; the simulator continues to "
                    "emit telemetry through its local OTEL/logging pipeline."
                ),
            )
        )
    if eventhub_block is not None:
        acc.diagnostics.append(
            ImportDiagnostic(
                status="adapted",
                scope=f"logger:{logger_id}",
                feature="eventhub",
                detail=(
                    "Logger Event Hub settings are descriptive only; the simulator continues to emit "
                    "telemetry through its local OTEL/logging pipeline."
                ),
            )
        )


def _import_diagnostic(res: TFResource, acc: _ImportAccumulator) -> None:
    diagnostic_id = str(res.values.get("identifier") or res.name).strip()
    if not diagnostic_id:
        return
    logger_reference = str(res.values.get("api_management_logger_id") or "").strip()
    logger_id = _resource_name_from_id(logger_reference, acc.id_to_name) if logger_reference else None
    acc.diagnostic_resources[diagnostic_id] = DiagnosticConfig(
        identifier=diagnostic_id,
        logger_id=logger_id,
        always_log_errors=_coerce_bool(res.values.get("always_log_errors")),
        backend_request=_diagnostic_http_message(res.values.get("backend_request")),
        backend_response=_diagnostic_http_message(res.values.get("backend_response")),
        frontend_request=_diagnostic_http_message(res.values.get("frontend_request")),
        frontend_response=_diagnostic_http_message(res.values.get("frontend_response")),
        http_correlation_protocol=(
            str(res.values.get("http_correlation_protocol")) if res.values.get("http_correlation_protocol") else None
        ),
        log_client_ip=_coerce_bool(res.values.get("log_client_ip")),
        sampling_percentage=_coerce_float(res.values.get("sampling_percentage")),
        verbosity=str(res.values.get("verbosity")) if res.values.get("verbosity") else None,
        operation_name_format=(
            str(res.values.get("operation_name_format")) if res.values.get("operation_name_format") else None
        ),
    )
    acc.diagnostics.append(
        ImportDiagnostic(
            status="supported",
            scope=f"diagnostic:{diagnostic_id}",
            feature="diagnostic",
            detail="Imported API Management diagnostic for read-only local inspection.",
        )
    )
    acc.diagnostics.append(
        ImportDiagnostic(
            status="adapted",
            scope=f"diagnostic:{diagnostic_id}",
            feature="runtime_settings",
            detail=(
                "Diagnostic sampling, header/body capture, and logger routing are descriptive only; "
                "runtime observability continues to use local traces and OTEL."
            ),
        )
    )


def _import_api_version_set(res: TFResource, acc: _ImportAccumulator) -> None:
    scheme_raw = str(res.values.get("versioning_scheme") or "Segment")
    try:
        scheme = ApiVersioningScheme(scheme_raw)
    except ValueError:
        acc.diagnostics.append(
            ImportDiagnostic(
                status="unsupported",
                scope=f"api-version-set:{res.name}",
                feature="versioning_scheme",
                detail=f"Unsupported versioning scheme: {scheme_raw}",
            )
        )
        return
    acc.api_version_sets[res.name] = ApiVersionSetConfig(
        display_name=str(res.values.get("display_name") or res.name),
        description=str(res.values.get("description")) if res.values.get("description") else None,
        versioning_scheme=scheme,
        version_header_name=(
            str(res.values.get("version_header_name")) if res.values.get("version_header_name") else None
        ),
        version_query_name=(
            str(res.values.get("version_query_name")) if res.values.get("version_query_name") else None
        ),
    )


def _import_backend(res: TFResource, acc: _ImportAccumulator) -> None:
    credentials = _first_block(res.values.get("credentials")) or {}
    authorization = _first_block(credentials.get("authorization")) or {}
    pool_members = _backend_pool_members(res.values)
    breaker = _backend_circuit_breaker(res.values)
    backend_type = str(res.values.get("type") or ("pool" if pool_members else "single"))
    acc.backends[res.name] = BackendConfig(
        url=str(res.values.get("url") or ("" if pool_members else http_url("upstream"))),
        description=str(res.values.get("description")) if res.values.get("description") else None,
        type=backend_type,
        pool=pool_members,
        circuit_breaker=breaker,
        authorization_scheme=(str(authorization.get("scheme")) if authorization.get("scheme") else None),
        authorization_parameter=(str(authorization.get("parameter")) if authorization.get("parameter") else None),
        header_credentials=_string_map(credentials.get("header")),
        query_credentials=_string_map(credentials.get("query")),
        client_certificate_thumbprints=[
            str(item) for item in (credentials.get("certificate") or []) if str(item).strip()
        ],
    )


def _apply_api_import_document(res: TFResource, acc: _ImportAccumulator, candidate: ApiConfig, api_name: str) -> None:
    """Fold an inline OpenAPI import into the API being built.

    A document that will not parse or will not load is recorded as a diagnostic
    and skipped: the API itself is still imported, just without its operations.
    """
    import_block = _api_import_block(res.values)
    if import_block is None:
        return

    def _unsupported(detail: str) -> None:
        acc.diagnostics.append(
            ImportDiagnostic(status="unsupported", scope=f"api:{api_name}", feature="api_import", detail=detail)
        )

    try:
        imported = parse_api_import(
            content_format=str(import_block.get("content_format") or ""),
            content_value=str(import_block.get("content_value") or ""),
            fetcher=acc.fetcher,
        )
    except ValueError as exc:
        _unsupported(str(exc))
        return
    except Exception as exc:  # noqa: BLE001 - a bad document must not fail the whole import
        _unsupported(f"Failed to load API import document: {exc}")
        return

    # An explicit service_url on the resource outranks the document's own.
    if imported.upstream_base_url and not res.values.get("service_url"):
        candidate.upstream_base_url = imported.upstream_base_url
    for operation in imported.operations:
        candidate.operations[operation.name] = OperationConfig(
            name=operation.name,
            method=operation.method,
            url_template=operation.url_template,
        )

    acc.diagnostics.append(
        ImportDiagnostic(
            status="supported",
            scope=f"api:{api_name}",
            feature="api_import",
            detail=f"Imported {len(imported.operations)} operations from {imported.format}.",
        )
    )
    for item in imported.diagnostics:
        acc.diagnostics.append(
            ImportDiagnostic(status="adapted", scope=f"api:{api_name}", feature="api_import", detail=item)
        )


def _merge_api_revision(
    acc: _ImportAccumulator,
    *,
    api_name: str,
    candidate: ApiConfig,
    revision: str,
    revision_metadata: ApiRevisionConfig,
) -> None:
    """Record another revision of an API already imported.

    The simulator serves one API per name, so revisions collapse: the current
    revision becomes the active API and carries over what the previous one held.
    """
    existing = acc.apis[api_name]
    existing.revisions[revision] = revision_metadata

    if revision_metadata.is_current and not existing.is_current:
        candidate.tags = existing.tags
        if not candidate.operations:
            candidate.operations = existing.operations
        candidate.schemas = existing.schemas
        candidate.releases = existing.releases
        candidate.revisions = dict(existing.revisions)
        acc.apis[api_name] = candidate

    acc.diagnostics.append(
        ImportDiagnostic(
            status="adapted",
            scope=f"api:{api_name}",
            feature="revisions",
            detail=(
                "Multiple API revisions are collapsed into one active local API while revision metadata is preserved."
            ),
        )
    )


def _import_api(res: TFResource, acc: _ImportAccumulator) -> None:
    api_name = str(res.values.get("name") or res.name)
    revision = str(res.values.get("revision") or "1")
    version_set_id = str(res.values.get("version_set_id") or "")
    source_api_id = str(res.values.get("source_api_id")) if res.values.get("source_api_id") else None
    revision_is_current = _coerce_bool(res.values.get("is_current"))
    revision_is_online = _coerce_bool(res.values.get("is_online"))
    subscription_header_names, subscription_query_param_names = _subscription_key_parameter_names(res.values)

    revision_metadata = ApiRevisionConfig(
        revision=revision,
        description=str(res.values.get("revision_description")) if res.values.get("revision_description") else None,
        is_current=revision_is_current,
        is_online=revision_is_online,
        source_api_id=source_api_id,
    )

    candidate = ApiConfig(
        name=api_name,
        path=str(res.values.get("path") or api_name),
        upstream_base_url=str(res.values.get("service_url") or http_url("upstream")),
        api_version_set=_resource_name_from_id(version_set_id, acc.id_to_name) if version_set_id else None,
        api_version=(str(res.values.get("version")) if res.values.get("version") else None),
        revision=revision,
        revision_description=revision_metadata.description,
        version_description=(
            str(res.values.get("version_description")) if res.values.get("version_description") else None
        ),
        source_api_id=source_api_id,
        is_current=revision_is_current,
        is_online=revision_is_online,
        subscription_header_names=subscription_header_names,
        subscription_query_param_names=subscription_query_param_names,
        revisions={revision: revision_metadata},
    )

    _apply_api_import_document(res, acc, candidate, api_name)

    if api_name not in acc.apis:
        acc.apis[api_name] = candidate
        return
    _merge_api_revision(
        acc, api_name=api_name, candidate=candidate, revision=revision, revision_metadata=revision_metadata
    )


def _link_api_schema(res: TFResource, acc: _ImportAccumulator) -> None:
    api_name = str(res.values.get("api_name") or "")
    if not api_name or api_name not in acc.apis:
        return
    schema_id = str(res.values.get("schema_id") or res.name)
    acc.apis[api_name].schemas[schema_id] = _api_schema(res.values)


def _link_api_operation(res: TFResource, acc: _ImportAccumulator) -> None:
    api_name = str(res.values.get("api_name") or "")
    if not api_name or api_name not in acc.apis:
        return
    op_id = str(res.values.get("operation_id") or res.name)
    method = str(res.values.get("method") or "GET")
    url_template = str(res.values.get("url_template") or "/")
    acc.apis[api_name].operations[op_id] = OperationConfig(
        name=op_id,
        method=method,
        url_template=url_template,
        description=str(res.values.get("description")) if res.values.get("description") else None,
        template_parameters=_parameter_blocks(res.values.get("template_parameter")),
        request=_request_metadata(res.values.get("request")),
        responses=_response_metadata(res.values.get("response")),
    )


def _link_product_api(res: TFResource, acc: _ImportAccumulator) -> None:
    product_id = str(res.values.get("product_id") or "")
    api_name = str(res.values.get("api_name") or "")
    if product_id and api_name and api_name in acc.apis and product_id not in acc.apis[api_name].products:
        acc.apis[api_name].products.append(product_id)


def _link_product_group(res: TFResource, acc: _ImportAccumulator) -> None:
    product_id = _resource_name_from_id(str(res.values.get("product_id") or ""), acc.id_to_name)
    group_name = str(res.values.get("group_name") or "").strip()
    if not product_id or product_id not in acc.products or not group_name:
        return
    created_placeholder = _ensure_group(acc.groups, group_name=group_name)
    if group_name not in acc.products[product_id].groups:
        acc.products[product_id].groups.append(group_name)
    acc.diagnostics.append(
        ImportDiagnostic(
            status="supported",
            scope=f"product:{product_id}",
            feature=f"group:{group_name}",
            detail="Imported product-group link.",
        )
    )
    if created_placeholder:
        acc.diagnostics.append(
            ImportDiagnostic(
                status="adapted",
                scope=f"group:{group_name}",
                feature="placeholder_group",
                detail="Created a local placeholder group because the product-group link had no separate group definition.",
            )
        )


def _link_group_user(res: TFResource, acc: _ImportAccumulator) -> None:
    group_name = str(res.values.get("group_name") or "").strip()
    user_id = (
        _resource_name_from_id(str(res.values.get("user_id") or ""), acc.id_to_name)
        or str(res.values.get("user_id") or "").strip()
    )
    if not group_name or not user_id:
        return
    created_group_placeholder = _ensure_group(acc.groups, group_name=group_name)
    created_user_placeholder = _ensure_user(acc.users, user_id=user_id)
    if user_id not in acc.groups[group_name].users:
        acc.groups[group_name].users.append(user_id)
    acc.diagnostics.append(
        ImportDiagnostic(
            status="supported",
            scope=f"group:{group_name}",
            feature=f"user:{user_id}",
            detail="Imported group-user link.",
        )
    )
    if created_group_placeholder:
        acc.diagnostics.append(
            ImportDiagnostic(
                status="adapted",
                scope=f"group:{group_name}",
                feature="placeholder_group",
                detail="Created a local placeholder group because the group-user link had no separate group definition.",
            )
        )
    if created_user_placeholder:
        acc.diagnostics.append(
            ImportDiagnostic(
                status="adapted",
                scope=f"user:{user_id}",
                feature="placeholder_user",
                detail="Created a local placeholder user because the group-user link had no separate user definition.",
            )
        )


def _link_api_tag(res: TFResource, acc: _ImportAccumulator) -> None:
    api_name = _resource_name_from_id(str(res.values.get("api_id") or ""), acc.id_to_name)
    tag_name = str(res.values.get("name") or "").strip()
    if not api_name or api_name not in acc.apis or not tag_name:
        return
    created_placeholder = _ensure_tag(acc.tags, tag_name=tag_name)
    if tag_name not in acc.apis[api_name].tags:
        acc.apis[api_name].tags.append(tag_name)
    acc.diagnostics.append(
        ImportDiagnostic(
            status="supported",
            scope=f"api:{api_name}",
            feature=f"tag:{tag_name}",
            detail="Imported API tag link.",
        )
    )
    if created_placeholder:
        acc.diagnostics.append(
            ImportDiagnostic(
                status="adapted",
                scope=f"tag:{tag_name}",
                feature="placeholder_tag",
                detail="Created a local tag placeholder because the API tag link had no separate tag definition.",
            )
        )


def _link_product_tag(res: TFResource, acc: _ImportAccumulator) -> None:
    product_id = _resource_name_from_id(str(res.values.get("api_management_product_id") or ""), acc.id_to_name)
    tag_name = str(res.values.get("name") or "").strip()
    if not product_id or product_id not in acc.products or not tag_name:
        return
    created_placeholder = _ensure_tag(acc.tags, tag_name=tag_name)
    if tag_name not in acc.products[product_id].tags:
        acc.products[product_id].tags.append(tag_name)
    acc.diagnostics.append(
        ImportDiagnostic(
            status="supported",
            scope=f"product:{product_id}",
            feature=f"tag:{tag_name}",
            detail="Imported product tag link.",
        )
    )
    if created_placeholder:
        acc.diagnostics.append(
            ImportDiagnostic(
                status="adapted",
                scope=f"tag:{tag_name}",
                feature="placeholder_tag",
                detail=("Created a local tag placeholder because the product tag link had no separate tag definition."),
            )
        )


def _link_api_operation_tag(res: TFResource, acc: _ImportAccumulator) -> None:
    operation_id = str(res.values.get("api_operation_id") or "")
    api_name = _arm_id_segment(operation_id, "apis")
    op_name = _arm_id_segment(operation_id, "operations")
    tag_name = str(res.values.get("name") or "").strip()
    display_name = str(res.values.get("display_name") or tag_name).strip() or tag_name
    if (
        not api_name
        or api_name not in acc.apis
        or not op_name
        or op_name not in acc.apis[api_name].operations
        or not tag_name
    ):
        return
    created_placeholder = _ensure_tag(acc.tags, tag_name=tag_name, display_name=display_name)
    if tag_name not in acc.apis[api_name].operations[op_name].tags:
        acc.apis[api_name].operations[op_name].tags.append(tag_name)
    acc.diagnostics.append(
        ImportDiagnostic(
            status="supported",
            scope=f"operation:{api_name}:{op_name}",
            feature=f"tag:{tag_name}",
            detail="Imported operation tag link.",
        )
    )
    if created_placeholder:
        acc.diagnostics.append(
            ImportDiagnostic(
                status="adapted",
                scope=f"tag:{tag_name}",
                feature="placeholder_tag",
                detail="Created a local tag definition from the operation tag resource.",
            )
        )


def _link_api_release(res: TFResource, acc: _ImportAccumulator) -> None:
    api_name, revision = _api_name_and_revision_from_resource_id(str(res.values.get("api_id") or ""))
    if not api_name or api_name not in acc.apis:
        return
    release_name = str(res.values.get("name") or res.name)
    acc.apis[api_name].releases[release_name] = ApiReleaseConfig(
        name=release_name,
        api_id=str(res.values.get("api_id")) if res.values.get("api_id") else None,
        notes=str(res.values.get("notes")) if res.values.get("notes") else None,
        revision=revision,
    )
    acc.diagnostics.append(
        ImportDiagnostic(
            status="supported",
            scope=f"api:{api_name}",
            feature=f"release:{release_name}",
            detail="Imported API release metadata.",
        )
    )


def _link_subscription(res: TFResource, acc: _ImportAccumulator) -> None:
    sub_id = str(res.values.get("subscription_id") or res.values.get("name") or res.name)
    if sub_id not in acc.subscriptions:
        return
    raw_product_id = res.values.get("product_id")
    if not isinstance(raw_product_id, str) or not raw_product_id:
        return
    product_id = _arm_id_segment(raw_product_id, "products") or raw_product_id
    subscription = acc.subscriptions[sub_id]
    if product_id in subscription.products:
        return
    if subscription.all_apis or subscription.service_scoped or subscription.api_id is not None:
        subscription.all_apis = False
        subscription.service_scoped = False
        subscription.api_id = None
        subscription.scope = None
    subscription.products.append(product_id)
    subscription.scope = SubscriptionScope.Product


def _link_api_policy(res: TFResource, acc: _ImportAccumulator) -> None:
    api_name = str(res.values.get("api_name") or "")
    xml = res.values.get("xml_content")
    if api_name in acc.apis and isinstance(xml, str) and xml:
        acc.apis[api_name].policies_xml = xml


def _link_api_operation_policy(res: TFResource, acc: _ImportAccumulator) -> None:
    api_name = str(res.values.get("api_name") or "")
    op_id = str(res.values.get("operation_id") or "")
    xml = res.values.get("xml_content")
    if not (api_name and op_id and isinstance(xml, str) and xml):
        return
    api = acc.apis.get(api_name)
    if api is None:
        return
    op = api.operations.get(op_id)
    if op is None:
        return
    op.policies_xml = xml


# Resources that stand on their own. Read before anything that references them.
_FIRST_PASS: dict[str, Callable[[TFResource, _ImportAccumulator], None]] = {
    "azurerm_api_management": _import_service,
    "azurerm_api_management_product": _import_product,
    "azurerm_api_management_group": _import_group,
    "azurerm_api_management_user": _import_user,
    "azurerm_api_management_tag": _import_tag,
    "azurerm_api_management_subscription": _import_subscription,
    "azurerm_api_management_named_value": _import_named_value,
    "azurerm_api_management_logger": _import_logger,
    "azurerm_api_management_diagnostic": _import_diagnostic,
    "azurerm_api_management_api_version_set": _import_api_version_set,
    "azurerm_api_management_backend": _import_backend,
    "azurerm_api_management_api": _import_api,
}

# Resources that attach one first-pass resource to another, or add policy to it.
_SECOND_PASS: dict[str, Callable[[TFResource, _ImportAccumulator], None]] = {
    "azurerm_api_management_api_schema": _link_api_schema,
    "azurerm_api_management_api_operation": _link_api_operation,
    "azurerm_api_management_product_api": _link_product_api,
    "azurerm_api_management_product_group": _link_product_group,
    "azurerm_api_management_group_user": _link_group_user,
    "azurerm_api_management_api_tag": _link_api_tag,
    "azurerm_api_management_product_tag": _link_product_tag,
    "azurerm_api_management_api_operation_tag": _link_api_operation_tag,
    "azurerm_api_management_api_release": _link_api_release,
    "azurerm_api_management_subscription": _link_subscription,
    "azurerm_api_management_api_policy": _link_api_policy,
    "azurerm_api_management_api_operation_policy": _link_api_operation_policy,
}


def _index_resource_names(resources: list[TFResource], acc: _ImportAccumulator) -> None:
    """Map both the ARM id and the Terraform name of every resource to its name.

    Associations reference their targets by whichever of the two the author had
    to hand, so both have to resolve.
    """
    for res in resources:
        resource_id = res.values.get("id")
        if isinstance(resource_id, str) and resource_id:
            acc.id_to_name[resource_id] = res.name
        acc.id_to_name[res.name] = res.name


def _import_azapi_resource(res: TFResource, acc: _ImportAccumulator) -> None:
    """Handle the AzAPI spelling of a resource the AzureRM provider also has."""
    arm_type = arm_resource_type(res)
    if arm_type == "Microsoft.ApiManagement/service":
        acc.service, mode, service_diagnostics = _import_azapi_service(res.values, res.name)
        acc.service_imported = True
        acc.diagnostics.extend(service_diagnostics)
        if mode is not None:
            acc.client_certificate_mode = mode
    elif arm_type in AZAPI_APIM_CHILD_EQUIVALENTS:
        acc.diagnostics.append(
            ImportDiagnostic(
                status="unsupported",
                scope=arm_type,
                feature="azapi_import",
                detail=(
                    "Detected APIM child resource via AzAPI, but the simulator only imports the "
                    f"AzureRM equivalent `{AZAPI_APIM_CHILD_EQUIVALENTS[arm_type]}` today."
                ),
            )
        )


def _import_gateway_policy(res: TFResource, acc: _ImportAccumulator) -> None:
    """Take the tenant-wide policy document, and say so for scopes not imported."""
    if res.type == "azurerm_api_management_policy":
        xml = res.values.get("xml_content")
        if isinstance(xml, str) and xml:
            acc.gateway_policy = xml
        return
    if res.type.endswith("_policy") and res.type not in {
        "azurerm_api_management_api_policy",
        "azurerm_api_management_api_operation_policy",
    }:
        acc.diagnostics.append(
            ImportDiagnostic(
                status="unsupported",
                scope=res.type,
                feature="policy_scope",
                detail="This policy scope is not imported into the simulator yet.",
            )
        )


def _report_dangling_logger_references(acc: _ImportAccumulator) -> None:
    for diagnostic_id, diagnostic in acc.diagnostic_resources.items():
        if diagnostic.logger_id and diagnostic.logger_id not in acc.loggers:
            acc.diagnostics.append(
                ImportDiagnostic(
                    status="adapted",
                    scope=f"diagnostic:{diagnostic_id}",
                    feature="logger_reference",
                    detail=("Referenced logger was not imported; keeping the resolved logger id for inspection only."),
                )
            )


def _subscription_key_names(acc: _ImportAccumulator) -> tuple[list[str], list[str]]:
    """Collect the subscription key header and query names every API asked for."""
    header_names: list[str] = []
    query_param_names: list[str] = []
    for api in acc.apis.values():
        for name in api.subscription_header_names or []:
            if name not in header_names:
                header_names.append(name)
        for name in api.subscription_query_param_names or []:
            if name not in query_param_names:
                query_param_names.append(name)
    return header_names, query_param_names


def _assemble_config(acc: _ImportAccumulator) -> GatewayConfig:
    header_names, query_param_names = _subscription_key_names(acc)
    subscription_payload: dict[str, Any] = {"required": True, "subscriptions": acc.subscriptions}
    if header_names:
        subscription_payload["header_names"] = header_names
    if query_param_names:
        subscription_payload["query_param_names"] = query_param_names

    cfg = GatewayConfig(
        allow_anonymous=True,
        service=acc.service,
        products=acc.products,
        named_values=acc.named_values,
        loggers=acc.loggers,
        diagnostics=acc.diagnostic_resources,
        users=acc.users,
        groups=acc.groups,
        api_version_sets=acc.api_version_sets,
        backends=acc.backends,
        tags=acc.tags,
        subscription=subscription_payload,
        apis=acc.apis,
        policies_xml=acc.gateway_policy,
    )
    if acc.client_certificate_mode is not None:
        cfg.client_certificate.mode = acc.client_certificate_mode
    if not header_names:
        cfg.subscription.header_names = ["Ocp-Apim-Subscription-Key"]
    if not query_param_names:
        cfg.subscription.query_param_names = ["subscription-key"]
    cfg.routes = cfg.materialize_routes()
    return validate_policy_config(cfg)


def import_from_tofu_show_json(
    tf: dict[str, Any],
    *,
    fetcher: Callable[[str], str] | None = None,
) -> ImportResult:
    """Build a tenant document from `tofu show -json` output.

    Three passes, in this order and for this reason: standalone resources first,
    then the associations and policies that reference them, then the tenant-wide
    policy. A resource type is handled by one entry in one of the dispatch
    tables above, never by another branch here.
    """
    resources = list(_iter_resources(tf))
    acc = _ImportAccumulator(fetcher=fetcher)

    _index_resource_names(resources, acc)

    for res in resources:
        handler = _FIRST_PASS.get(res.type)
        if handler is not None:
            handler(res, acc)
        _import_azapi_resource(res, acc)

    for res in resources:
        handler = _SECOND_PASS.get(res.type)
        if handler is not None:
            handler(res, acc)

    for res in resources:
        _import_gateway_policy(res, acc)

    _report_dangling_logger_references(acc)
    return ImportResult(
        config=_assemble_config(acc), diagnostics=acc.diagnostics, service_imported=acc.service_imported
    )


def config_from_tofu_show_json(
    tf: dict[str, Any],
    *,
    fetcher: Callable[[str], str] | None = None,
) -> GatewayConfig:
    return import_from_tofu_show_json(tf, fetcher=fetcher).config
