from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from app.config import (
    ApiConfig,
    ApiVersioningScheme,
    ApiVersionSetConfig,
    BackendConfig,
    GatewayConfig,
    KeyVaultNamedValueConfig,
    NamedValueConfig,
    OperationConfig,
    ProductConfig,
    Subscription,
    SubscriptionKeyPair,
)
from app.openapi_import import parse_api_import


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


def _api_import_block(values: dict[str, Any]) -> dict[str, Any] | None:
    return _first_block(values.get("import"))


def _subscription_key_parameter_names(values: dict[str, Any]) -> tuple[list[str] | None, list[str] | None]:
    block = _first_block(values.get("subscription_key_parameter_names"))
    if block is None:
        return None, None
    header = str(block.get("header") or "").strip() or None
    query = str(block.get("query") or "").strip() or None
    return ([header] if header else None, [query] if query else None)


def import_from_tofu_show_json(
    tf: dict[str, Any],
    *,
    fetcher: Callable[[str], str] | None = None,
) -> ImportResult:
    resources = _iter_resources(tf)
    diagnostics: list[ImportDiagnostic] = []

    products: dict[str, ProductConfig] = {}
    subscriptions: dict[str, Subscription] = {}
    named_values: dict[str, NamedValueConfig] = {}
    api_version_sets: dict[str, ApiVersionSetConfig] = {}
    backends: dict[str, BackendConfig] = {}
    apis: dict[str, ApiConfig] = {}
    id_to_name: dict[str, str] = {}

    for res in resources:
        resource_id = res.values.get("id")
        if isinstance(resource_id, str) and resource_id:
            id_to_name[resource_id] = res.name
        id_to_name[res.name] = res.name

    # ---- First pass: base resources ----
    for res in resources:
        if res.type == "azurerm_api_management_product":
            product_id = str(res.values.get("product_id") or res.name)
            display_name = str(res.values.get("display_name") or product_id)
            subscription_required = res.values.get("subscription_required")
            require_subscription = bool(subscription_required) if subscription_required is not None else True
            products[product_id] = ProductConfig(name=display_name, require_subscription=require_subscription)

        if res.type == "azurerm_api_management_subscription":
            sub_id = str(res.values.get("subscription_id") or res.values.get("name") or res.name)
            display_name = str(res.values.get("display_name") or res.values.get("name") or sub_id)
            primary = str(res.values.get("primary_key") or "")
            secondary = str(res.values.get("secondary_key") or "")
            subscriptions[sub_id] = Subscription(
                id=sub_id,
                name=display_name,
                keys=SubscriptionKeyPair(primary=primary, secondary=secondary),
            )

        if res.type == "azurerm_api_management_named_value":
            name = str(res.values.get("display_name") or res.values.get("name") or res.name)
            secret = bool(res.values.get("secret"))
            key_vault_block = _first_block(res.values.get("value_from_key_vault"))
            value_from_key_vault = None
            if key_vault_block is not None and key_vault_block.get("secret_id"):
                value_from_key_vault = KeyVaultNamedValueConfig(
                    secret_id=str(key_vault_block["secret_id"]),
                    identity_client_id=(
                        str(key_vault_block.get("identity_client_id"))
                        if key_vault_block.get("identity_client_id")
                        else None
                    ),
                )
                diagnostics.append(
                    ImportDiagnostic(
                        status="adapted",
                        scope=f"named-value:{name}",
                        feature="value_from_key_vault",
                        detail="Key Vault-backed named values require APIM_NAMED_VALUE_<NAME> env overrides locally.",
                    )
                )
            value = res.values.get("value")
            named_values[name] = NamedValueConfig(
                value=str(value) if value is not None else None,
                secret=secret,
                value_from_key_vault=value_from_key_vault,
            )

        if res.type == "azurerm_api_management_api_version_set":
            scheme_raw = str(res.values.get("versioning_scheme") or "Segment")
            try:
                scheme = ApiVersioningScheme(scheme_raw)
            except ValueError:
                diagnostics.append(
                    ImportDiagnostic(
                        status="unsupported",
                        scope=f"api-version-set:{res.name}",
                        feature="versioning_scheme",
                        detail=f"Unsupported versioning scheme: {scheme_raw}",
                    )
                )
                continue
            api_version_sets[res.name] = ApiVersionSetConfig(
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

        if res.type == "azurerm_api_management_backend":
            credentials = _first_block(res.values.get("credentials")) or {}
            authorization = _first_block(credentials.get("authorization")) or {}
            backends[res.name] = BackendConfig(
                url=str(res.values.get("url") or "http://upstream"),
                description=str(res.values.get("description")) if res.values.get("description") else None,
                authorization_scheme=(str(authorization.get("scheme")) if authorization.get("scheme") else None),
                authorization_parameter=(
                    str(authorization.get("parameter")) if authorization.get("parameter") else None
                ),
                header_credentials=_string_map(credentials.get("header")),
                query_credentials=_string_map(credentials.get("query")),
                client_certificate_thumbprints=[
                    str(item) for item in (credentials.get("certificate") or []) if str(item).strip()
                ],
            )

        if res.type == "azurerm_api_management_api":
            api_name = str(res.values.get("name") or res.name)
            path = str(res.values.get("path") or api_name)
            upstream = str(res.values.get("service_url") or "http://upstream")
            subscription_header_names, subscription_query_param_names = _subscription_key_parameter_names(res.values)
            version_set_id = str(res.values.get("version_set_id") or "")
            version_set_name = _resource_name_from_id(version_set_id, id_to_name) if version_set_id else None

            api = ApiConfig(
                name=api_name,
                path=path,
                upstream_base_url=upstream,
                api_version_set=version_set_name,
                api_version=(str(res.values.get("version")) if res.values.get("version") else None),
                subscription_header_names=subscription_header_names,
                subscription_query_param_names=subscription_query_param_names,
            )

            import_block = _api_import_block(res.values)
            if import_block is not None:
                content_format = str(import_block.get("content_format") or "")
                content_value = str(import_block.get("content_value") or "")
                try:
                    imported = parse_api_import(
                        content_format=content_format,
                        content_value=content_value,
                        fetcher=fetcher,
                    )
                except ValueError as exc:
                    diagnostics.append(
                        ImportDiagnostic(
                            status="unsupported",
                            scope=f"api:{api_name}",
                            feature="api_import",
                            detail=str(exc),
                        )
                    )
                except Exception as exc:
                    diagnostics.append(
                        ImportDiagnostic(
                            status="unsupported",
                            scope=f"api:{api_name}",
                            feature="api_import",
                            detail=f"Failed to load API import document: {exc}",
                        )
                    )
                else:
                    if imported.upstream_base_url and not res.values.get("service_url"):
                        api.upstream_base_url = imported.upstream_base_url
                    for operation in imported.operations:
                        api.operations[operation.name] = OperationConfig(
                            name=operation.name,
                            method=operation.method,
                            url_template=operation.url_template,
                        )
                    diagnostics.append(
                        ImportDiagnostic(
                            status="supported",
                            scope=f"api:{api_name}",
                            feature="api_import",
                            detail=f"Imported {len(imported.operations)} operations from {imported.format}.",
                        )
                    )
                    for item in imported.diagnostics:
                        diagnostics.append(
                            ImportDiagnostic(
                                status="adapted",
                                scope=f"api:{api_name}",
                                feature="api_import",
                                detail=item,
                            )
                        )

            apis[api_name] = api

    # ---- Second pass: children, associations, and policies ----
    for res in resources:
        if res.type == "azurerm_api_management_api_operation":
            api_name = str(res.values.get("api_name") or "")
            if not api_name or api_name not in apis:
                continue
            op_id = str(res.values.get("operation_id") or res.name)
            method = str(res.values.get("method") or "GET")
            url_template = str(res.values.get("url_template") or "/")
            apis[api_name].operations[op_id] = OperationConfig(
                name=op_id,
                method=method,
                url_template=url_template,
            )

        if res.type == "azurerm_api_management_product_api":
            product_id = str(res.values.get("product_id") or "")
            api_name = str(res.values.get("api_name") or "")
            if product_id and api_name and api_name in apis and product_id not in apis[api_name].products:
                apis[api_name].products.append(product_id)

        if res.type == "azurerm_api_management_subscription":
            sub_id = str(res.values.get("subscription_id") or res.values.get("name") or res.name)
            if sub_id not in subscriptions:
                continue
            product_id = res.values.get("product_id")
            if isinstance(product_id, str) and product_id and product_id not in subscriptions[sub_id].products:
                subscriptions[sub_id].products.append(product_id)

        if res.type == "azurerm_api_management_api_policy":
            api_name = str(res.values.get("api_name") or "")
            xml = res.values.get("xml_content")
            if api_name in apis and isinstance(xml, str) and xml:
                apis[api_name].policies_xml = xml

        if res.type == "azurerm_api_management_api_operation_policy":
            api_name = str(res.values.get("api_name") or "")
            op_id = str(res.values.get("operation_id") or "")
            xml = res.values.get("xml_content")
            if not (api_name and op_id and isinstance(xml, str) and xml):
                continue
            api = apis.get(api_name)
            if api is None:
                continue
            op = api.operations.get(op_id)
            if op is None:
                continue
            op.policies_xml = xml

    gateway_policy: str | None = None
    for res in resources:
        if res.type == "azurerm_api_management_policy":
            xml = res.values.get("xml_content")
            if isinstance(xml, str) and xml:
                gateway_policy = xml
            continue
        if res.type.endswith("_policy") and res.type not in {
            "azurerm_api_management_api_policy",
            "azurerm_api_management_api_operation_policy",
        }:
            diagnostics.append(
                ImportDiagnostic(
                    status="unsupported",
                    scope=res.type,
                    feature="policy_scope",
                    detail="This policy scope is not imported into the simulator yet.",
                )
            )

    header_names: list[str] = []
    query_param_names: list[str] = []
    for api in apis.values():
        if api.subscription_header_names:
            for name in api.subscription_header_names:
                if name not in header_names:
                    header_names.append(name)
        if api.subscription_query_param_names:
            for name in api.subscription_query_param_names:
                if name not in query_param_names:
                    query_param_names.append(name)

    subscription_payload: dict[str, Any] = {
        "required": True,
        "subscriptions": subscriptions,
    }
    if header_names:
        subscription_payload["header_names"] = header_names
    if query_param_names:
        subscription_payload["query_param_names"] = query_param_names

    cfg = GatewayConfig(
        allow_anonymous=True,
        products=products,
        named_values=named_values,
        api_version_sets=api_version_sets,
        backends=backends,
        subscription=subscription_payload,
        apis=apis,
        policies_xml=gateway_policy,
    )
    if not header_names:
        cfg.subscription.header_names = ["Ocp-Apim-Subscription-Key", "X-Ocp-Apim-Subscription-Key"]
    if not query_param_names:
        cfg.subscription.query_param_names = ["subscription-key"]
    cfg.routes = cfg.materialize_routes()
    return ImportResult(config=cfg, diagnostics=diagnostics)


def config_from_tofu_show_json(
    tf: dict[str, Any],
    *,
    fetcher: Callable[[str], str] | None = None,
) -> GatewayConfig:
    return import_from_tofu_show_json(tf, fetcher=fetcher).config
