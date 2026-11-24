"""Workspace isolation: ownership, cross-reference rules and gateway placement.

Azure API Management workspaces (Premium, Premium v2 and the other v2 tiers)
let separate teams own APIs, products, subscriptions, named values, backends
and policy fragments inside one service. The rules modelled here come from
https://learn.microsoft.com/en-us/azure/api-management/workspaces-overview:

- Resource names are unique across the whole service, so the simulator keeps one
  dictionary per resource type and records the owner on each resource.
- A workspace resource may reference resources in the same workspace only. It
  may not use another workspace's resources, nor service-level named values,
  backends or fragments. Service-level users and groups stay shared.
- Workspace APIs run the policy chain global -> workspace -> product -> API ->
  operation.
- A workspace's APIs are served only by the gateways it is associated with. A
  workspace gateway serves only its workspaces' APIs.

Because names are unique, checking every reference when the config is loaded
or changed is enough to keep runtime lookups inside one workspace.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from defusedxml import ElementTree

from app.config import MANAGED_GATEWAY_ID

if TYPE_CHECKING:
    from app.config import GatewayConfig, RouteConfig

_MAX_REPORTED = 10


def owner_label(workspace_id: str | None) -> str:
    return f"workspace {workspace_id}" if workspace_id else "the service level"


def api_workspace(cfg: GatewayConfig, api_id: str | None) -> str | None:
    api = cfg.apis.get(api_id or "")
    return api.workspace if api is not None else None


def route_workspace(cfg: GatewayConfig, route: RouteConfig) -> str | None:
    return api_workspace(cfg, route.api_id)


def fragment_owners(cfg: GatewayConfig) -> dict[str, str]:
    return {
        fragment_id: workspace_id
        for workspace_id, workspace in cfg.workspaces.items()
        for fragment_id in workspace.policy_fragments
    }


def workspaces_enabled(cfg: GatewayConfig) -> bool:
    return bool(cfg.workspaces or cfg.workspace_gateways)


def route_served_on_gateway(cfg: GatewayConfig, route: RouteConfig, gateway_id: str) -> bool:
    """Whether the gateway a request arrived on serves this route's API."""
    api = cfg.apis.get(route.api_id or "")
    if api is not None and api.gateways is not None:
        # An explicit assignment wins over workspace placement.
        return gateway_id in api.gateways
    workspace_id = route_workspace(cfg, route)
    if workspace_id is None:
        return gateway_id == MANAGED_GATEWAY_ID
    workspace = cfg.workspaces.get(workspace_id)
    return workspace is not None and gateway_id in workspace.gateways


def backend_visible_to_api(cfg: GatewayConfig, backend_id: str, api_id: str | None) -> bool:
    """A workspace backend is usable only by its own workspace's APIs."""
    backend = cfg.backends.get(backend_id)
    if backend is None or backend.workspace is None:
        return backend is not None
    return backend.workspace == api_workspace(cfg, api_id)


def subscription_workspace(cfg: GatewayConfig, subscription_id: str | None) -> str | None:
    if not subscription_id:
        return None
    subscription = cfg.subscription.find_by_id(subscription_id)
    return subscription.workspace if subscription is not None else None


def control_plane_with_owned_apis(cfg: GatewayConfig) -> Any:
    """The control-plane config with each workspace's owned APIs added to its grants.

    Operator grants with ``workspace_id`` then cover the APIs a workspace owns,
    as well as any listed in the older ``control_plane.workspace_apis`` map.
    """
    if not cfg.workspaces:
        return cfg.control_plane
    merged = {key: list(values) for key, values in cfg.control_plane.workspace_apis.items()}
    for api_id, api in cfg.apis.items():
        if api.workspace is not None and api_id not in merged.setdefault(api.workspace, []):
            merged[api.workspace].append(api_id)
    return cfg.control_plane.model_copy(update={"workspace_apis": merged})


def workspace_resources(cfg: GatewayConfig, workspace_id: str) -> dict[str, list[str]]:
    """The IDs of every resource a workspace owns, by resource type."""
    workspace = cfg.workspaces.get(workspace_id)
    return {
        "apis": sorted(key for key, item in cfg.apis.items() if item.workspace == workspace_id),
        "products": sorted(key for key, item in cfg.products.items() if item.workspace == workspace_id),
        "subscriptions": sorted(
            item.id for item in cfg.subscription.subscriptions.values() if item.workspace == workspace_id
        ),
        "named_values": sorted(key for key, item in cfg.named_values.items() if item.workspace == workspace_id),
        "backends": sorted(key for key, item in cfg.backends.items() if item.workspace == workspace_id),
        "policy_fragments": sorted(workspace.policy_fragments) if workspace is not None else [],
    }


def _service_documents(cfg: GatewayConfig) -> Iterator[tuple[str, str | None, str]]:
    for index, xml in enumerate(cfg.policies_xml_documents):
        if xml:
            yield f"gateway document {index}", None, xml
    if cfg.policies_xml:
        yield "gateway policy", None, cfg.policies_xml
    for workspace_id, workspace in cfg.workspaces.items():
        if workspace.policies_xml:
            yield f"workspace {workspace_id} policy", workspace_id, workspace.policies_xml
    for product_id, product in cfg.products.items():
        if product.policies_xml:
            yield f"product {product_id} policy", product.workspace, product.policies_xml


def _api_documents(api_id: str, api: Any) -> Iterator[tuple[str, str | None, str]]:
    if api.policies_xml:
        yield f"API {api_id} policy", api.workspace, api.policies_xml
    for operation_id, operation in api.operations.items():
        if operation.policies_xml:
            yield f"operation {api_id}:{operation_id} policy", api.workspace, operation.policies_xml
    resolvers = api.graphql.resolvers if api.graphql is not None else {}
    for resolver_id, resolver in resolvers.items():
        yield f"API {api_id} resolver {resolver_id}", api.workspace, resolver.policies_xml


def _policy_documents(cfg: GatewayConfig) -> Iterator[tuple[str, str | None, str]]:
    """Every policy document with its location and owning scope."""
    yield from _service_documents(cfg)
    for api_id, api in cfg.apis.items():
        yield from _api_documents(api_id, api)
    owners = fragment_owners(cfg)
    for fragment_id, xml in cfg.policy_fragments.items():
        yield f"policy fragment {fragment_id}", owners.get(fragment_id), f"<fragment>{xml}</fragment>"


def _policy_references(cfg: GatewayConfig, xml: str) -> Iterator[tuple[str, str]]:
    """(resource type, ID) pairs a policy document names literally."""
    from app.named_values import NAMED_VALUE_PATTERN, _find_named_value, resolve_named_values_in_text

    for match in NAMED_VALUE_PATTERN.finditer(xml):
        identifier, entry = _find_named_value(cfg, match.group(1).strip())
        if entry is not None:
            yield "named value", identifier
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return  # Syntax errors are reported by the policy validator.
    for element in root.iter("include-fragment"):
        yield "policy fragment", resolve_named_values_in_text(element.get("fragment-id", ""), cfg)
    for element in root.iter("set-backend-service"):
        backend_id = resolve_named_values_in_text(element.get("backend-id", ""), cfg)
        # A policy expression picks the backend at runtime; the gateway checks it then.
        if backend_id and not backend_id.lstrip().startswith("@"):
            yield "backend", backend_id


def _resource_owner(cfg: GatewayConfig, kind: str, identifier: str, fragments: dict[str, str]) -> tuple[bool, Any]:
    """(exists, owner) for one referenced resource."""
    stores: dict[str, Any] = {
        "named value": cfg.named_values,
        "backend": cfg.backends,
        "product": cfg.products,
        "API": cfg.apis,
    }
    if kind == "policy fragment":
        return identifier in cfg.policy_fragments, fragments.get(identifier)
    item = stores[kind].get(identifier)
    return item is not None, (item.workspace if item is not None else None)


def _api_references(api_id: str, api: Any) -> Iterator[tuple[str, str | None, str, str]]:
    for product_id in api.products:
        yield f"API {api_id}", api.workspace, "product", product_id
    if api.backend:
        yield f"API {api_id}", api.workspace, "backend", api.backend
    for operation_id, operation in api.operations.items():
        location = f"operation {api_id}:{operation_id}"
        for product_id in operation.products or []:
            yield location, api.workspace, "product", product_id
        if operation.backend:
            yield location, api.workspace, "backend", operation.backend


def _structural_references(cfg: GatewayConfig) -> Iterator[tuple[str, str | None, str, str]]:
    """(location, owner, resource type, ID) for references held in config fields."""
    for api_id, api in cfg.apis.items():
        yield from _api_references(api_id, api)
    for backend_id, backend in cfg.backends.items():
        for member in backend.pool:
            yield f"backend pool {backend_id}", backend.workspace, "backend", member.backend_id
    for subscription in cfg.subscription.subscriptions.values():
        location = f"subscription {subscription.id}"
        for product_id in subscription.products:
            yield location, subscription.workspace, "product", product_id
        if subscription.api_id:
            yield location, subscription.workspace, "API", subscription.api_id


def _ownership_errors(cfg: GatewayConfig) -> list[str]:
    errors: list[str] = []
    owned: list[tuple[str, str | None]] = [
        *((f"API {key}", item.workspace) for key, item in cfg.apis.items()),
        *((f"product {key}", item.workspace) for key, item in cfg.products.items()),
        *((f"named value {key}", item.workspace) for key, item in cfg.named_values.items()),
        *((f"backend {key}", item.workspace) for key, item in cfg.backends.items()),
        *((f"subscription {item.id}", item.workspace) for item in cfg.subscription.subscriptions.values()),
    ]
    errors.extend(
        f"{label} names unknown workspace {workspace_id}"
        for label, workspace_id in owned
        if workspace_id is not None and workspace_id not in cfg.workspaces
    )
    claimed: dict[str, str] = {}
    for workspace_id, workspace in cfg.workspaces.items():
        for fragment_id in workspace.policy_fragments:
            if fragment_id not in cfg.policy_fragments:
                errors.append(f"workspace {workspace_id} owns unknown policy fragment {fragment_id}")
            elif fragment_id in claimed:
                errors.append(
                    f"policy fragment {fragment_id} is owned by both workspace {claimed[fragment_id]} "
                    f"and workspace {workspace_id}"
                )
            claimed.setdefault(fragment_id, workspace_id)
        for gateway_id in workspace.gateways:
            if gateway_id != MANAGED_GATEWAY_ID and gateway_id not in cfg.workspace_gateways:
                errors.append(f"workspace {workspace_id} names unknown gateway {gateway_id}")
    if MANAGED_GATEWAY_ID in cfg.workspace_gateways:
        errors.append(f"workspace gateway ID {MANAGED_GATEWAY_ID} is reserved for the managed gateway")
    return errors


def _gateway_assignment_errors(cfg: GatewayConfig) -> list[str]:
    known = {MANAGED_GATEWAY_ID, *cfg.gateways, *cfg.workspace_gateways}
    return [
        f"API {api_id} names unknown gateway {gateway_id}"
        for api_id, api in cfg.apis.items()
        for gateway_id in api.gateways or []
        if gateway_id not in known
    ]


def _host_name_errors(cfg: GatewayConfig) -> list[str]:
    seen: dict[str, str] = {}
    errors: list[str] = []
    for gateway_id, gateway in cfg.workspace_gateways.items():
        for host in gateway.effective_host_names(gateway_id):
            normalized = host.strip().lower()
            if normalized in seen and seen[normalized] != gateway_id:
                errors.append(f"host name {host} is used by workspace gateways {seen[normalized]} and {gateway_id}")
            seen.setdefault(normalized, gateway_id)
    return errors


def _reference_errors(cfg: GatewayConfig) -> list[str]:
    fragments = fragment_owners(cfg)
    errors: list[str] = []
    references = [
        *_structural_references(cfg),
        *(
            (location, owner, kind, identifier)
            for location, owner, xml in _policy_documents(cfg)
            for kind, identifier in _policy_references(cfg, xml)
        ),
    ]
    for location, owner, kind, identifier in references:
        exists, target_owner = _resource_owner(cfg, kind, identifier, fragments)
        if exists and target_owner != owner:
            errors.append(
                f"{location} in {owner_label(owner)} references {kind} {identifier} in {owner_label(target_owner)}"
            )
    return errors


def validate_workspace_isolation(cfg: GatewayConfig) -> None:
    """Reject ownership and cross-workspace references Azure would refuse."""
    assignment_errors = _gateway_assignment_errors(cfg)
    if not workspaces_enabled(cfg) and not _any_owner_field(cfg) and not assignment_errors:
        return
    errors = [*assignment_errors, *_ownership_errors(cfg), *_host_name_errors(cfg), *_reference_errors(cfg)]
    if errors:
        unique = list(dict.fromkeys(errors))
        shown = "; ".join(unique[:_MAX_REPORTED])
        more = f" (and {len(unique) - _MAX_REPORTED} more)" if len(unique) > _MAX_REPORTED else ""
        raise ValueError(f"Workspace isolation: {shown}{more}")


def _any_owner_field(cfg: GatewayConfig) -> bool:
    return any(
        item.workspace is not None
        for store in (cfg.apis, cfg.products, cfg.named_values, cfg.backends, cfg.subscription.subscriptions)
        for item in store.values()
    )
