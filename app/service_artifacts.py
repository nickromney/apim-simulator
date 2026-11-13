"""Portable service configuration with target-local dependencies."""

from __future__ import annotations

import difflib
import hashlib
import hmac
import json
import secrets
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from app.api_artifacts import ApiArtifactRequest, export_artifact, stage_artifact
from app.config import ApiConfig, GatewayConfig, ProductConfig
from app.named_values import mask_secret_data
from app.policy_fragments import validate_fragment_references
from app.product_artifacts import (
    NamedValueMetadata,
    ProductApiArtifact,
    ProductOverrides,
    _dependencies,
    _stage_dependencies,
)

if TYPE_CHECKING:
    from app.management_service import ManagementService


class ServiceApiArtifact(ProductApiArtifact):
    products: list[str]


class ServiceArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int
    products: dict[str, dict[str, Any]]
    apis: dict[str, ServiceApiArtifact]
    gateway_policy: str | None
    gateway_policy_documents: list[str]
    policy_fragments: dict[str, str]
    backends: list[str]
    named_values: dict[str, NamedValueMetadata]


class ServiceArtifactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact: ServiceArtifact
    overrides: ProductOverrides = Field(default_factory=ProductOverrides)
    baseline: ServiceArtifact | None = None
    prune: StrictBool = False
    reviewed: StrictBool = False


_PLAN_KEY = secrets.token_bytes(32)


def reviewed_etag(cfg: GatewayConfig, body: ServiceArtifactRequest) -> str:
    if body.prune and body.baseline is None:
        raise HTTPException(400, "Prune requires a baseline artifact")
    raw = json.dumps([cfg.model_dump(mode="json"), body.model_dump(mode="json")], separators=(",", ":"))
    return f'"{hmac.new(_PLAN_KEY, raw.encode(), hashlib.sha256).hexdigest()}"'


def deletion_candidates(cfg: GatewayConfig, body: ServiceArtifactRequest) -> dict[str, list[str]]:
    result = {}
    for kind in ("apis", "products", "policy_fragments"):
        baseline = getattr(body.baseline, kind) if body.baseline else {}
        result[kind] = sorted((set(baseline) - set(getattr(body.artifact, kind))) & set(getattr(cfg, kind)))
    return result


def unlink_candidates(cfg: GatewayConfig, body: ServiceArtifactRequest) -> dict[str, list[str]]:
    if body.baseline is None:
        return {}
    result = {}
    for api_id in body.artifact.apis.keys() & body.baseline.apis.keys() & cfg.apis.keys():
        removed = (set(body.baseline.apis[api_id].products) - set(body.artifact.apis[api_id].products)) & set(
            cfg.apis[api_id].products
        )
        if removed:
            result[api_id] = sorted(removed)
    return dict(sorted(result.items()))


def service_etag(cfg: GatewayConfig) -> str:
    raw = json.dumps(cfg.model_dump(mode="json"), separators=(",", ":"))
    return f'"{hashlib.sha256(raw.encode()).hexdigest()}"'


def _api_portable(api: ApiConfig) -> None:
    for operation in api.operations.values():
        if operation.products is not None:
            raise HTTPException(
                400, "Operation-level product membership cannot be represented; move membership to the API"
            )
        if (
            operation.backend is not None
            or operation.upstream_base_url is not None
            or operation.upstream_path_prefix is not None
        ):
            raise HTTPException(400, "Operation-level backend settings cannot be represented; move routing to the API")


def _policies(cfg: GatewayConfig, api_ids, product_ids, fragments) -> list[str | None]:
    policies = [cfg.policies_xml, *cfg.policies_xml_documents]
    policies.extend(cfg.products[identifier].policies_xml for identifier in product_ids)
    for identifier in api_ids:
        api = cfg.apis[identifier]
        policies.extend([api.policies_xml, *(op.policies_xml for op in api.operations.values())])
    policies.extend(f"<fragment>{xml}</fragment>" for xml in fragments.values())
    return policies


def export_service_artifact(cfg: GatewayConfig) -> dict[str, Any]:
    if cfg.routes and not cfg.apis:
        raise HTTPException(400, "Legacy route-only configuration cannot be represented as a service artifact")
    apis = {}
    for api_id, api in cfg.apis.items():
        if set(api.products) - set(cfg.products):
            raise HTTPException(400, "API product links reference missing products")
        _api_portable(api)
        apis[api_id] = {
            "artifact": export_artifact(cfg, api_id),
            "backend": api.backend,
            "products": list(dict.fromkeys(api.products)),
        }
    policies = _policies(cfg, apis, cfg.products, cfg.policy_fragments)
    backends, values = _dependencies(
        cfg, policies, {api.backend for api in cfg.apis.values() if api.backend}, allow_fragments=True
    )
    result = {
        "schema_version": 1,
        "products": {
            key: product.model_dump(mode="json", exclude={"groups", "tags"}) for key, product in cfg.products.items()
        },
        "apis": apis,
        "gateway_policy": cfg.policies_xml,
        "gateway_policy_documents": cfg.policies_xml_documents,
        "policy_fragments": cfg.policy_fragments,
        "backends": backends,
        "named_values": values,
    }
    if mask_secret_data(result, cfg) != result:
        raise HTTPException(400, "Artifact contains a known secret; replace it with a named value reference")
    return result


def _stage_products(cfg: GatewayConfig, products: dict[str, dict[str, Any]]) -> None:
    fields = set(ProductConfig.model_fields) - {"groups", "tags"}
    for identifier, metadata in products.items():
        if not {"name", "state", "require_subscription"} <= metadata.keys() <= fields:
            raise HTTPException(400, "Invalid product artifact metadata")
        existing = cfg.products.get(identifier)
        try:
            cfg.products[identifier] = ProductConfig.model_validate(
                {**metadata, "groups": existing.groups if existing else [], "tags": existing.tags if existing else []}
            )
        except ValidationError as exc:
            raise HTTPException(400, "Invalid product artifact metadata") from exc


def _merge_order(existing: list[str], managed: list[str]) -> list[str]:
    """Replace managed slots in artifact order, preserving target-only slots."""
    managed_set = set(managed)
    incoming = iter(managed)
    result = [next(incoming) if item in managed_set else item for item in dict.fromkeys(existing)]
    result.extend(incoming)
    return result


def _stage_apis(
    manager: ManagementService,
    cfg: GatewayConfig,
    artifact: ServiceArtifact,
    overrides: ProductOverrides,
    reviewed_unlinks: dict[str, list[str]],
) -> GatewayConfig:
    for api_id, entry in artifact.apis.items():
        target = cfg.apis.get(api_id)
        if len(entry.products) != len(set(entry.products)) or set(entry.products) - set(artifact.products):
            raise HTTPException(400, "API product links must reference bundle products")
        if target:
            _api_portable(target)
            if (
                (set(target.products) & set(artifact.products))
                - set(entry.products)
                - set(reviewed_unlinks.get(api_id, []))
            ):
                raise HTTPException(400, "Removing product API links is not supported")
        upstream = overrides.api_upstreams.get(api_id)
        if entry.backend is None and target and target.backend and upstream is None:
            raise HTTPException(400, "An API switching to direct routing requires an upstream override")
        cfg, _ = stage_artifact(
            manager,
            cfg,
            api_id,
            ApiArtifactRequest(artifact=entry.artifact, upstream_base_url=upstream),
            defer_validation=True,
            backend=entry.backend,
        )
        api = cfg.apis[api_id]
        api.backend = entry.backend
        api.products = _merge_order(api.products, entry.products)
    cfg.apis = {identifier: cfg.apis[identifier] for identifier in _merge_order(list(cfg.apis), list(artifact.apis))}
    return cfg


def _check_prune_references(cfg: GatewayConfig, candidates: dict[str, list[str]]) -> None:  # noqa: C901 - checks independent retained references
    deleted_apis = set(candidates["apis"])
    deleted_products = set(candidates["products"])
    for subscription in cfg.subscription.subscriptions.values():
        if subscription.api_id in deleted_apis or set(subscription.products) & deleted_products:
            raise HTTPException(400, "Cannot prune a resource referenced by a subscription")
    for api in cfg.apis.values():
        if set(api.products) & deleted_products:
            raise HTTPException(400, "Cannot prune a product linked to a retained API")
        references = [
            api.source_api_id,
            *(r.source_api_id for r in api.revisions.values()),
            *(r.api_id for r in api.releases.values()),
        ]
        for reference in references:
            if reference and reference.rsplit("/", 1)[-1].split(";rev=", 1)[0] in deleted_apis:
                raise HTTPException(400, "Cannot prune an API referenced by a retained API revision or release")
        if not deleted_products and not candidates["policy_fragments"]:
            continue
        current_id = api.revision or next((key for key, rev in api.revisions.items() if rev.is_current), None)
        for revision_id, revision in api.revisions.items():
            if revision_id == current_id or not revision.definition:
                continue
            try:
                snapshot = ApiConfig.model_validate({**revision.definition, "revisions": {}, "releases": {}})
                if set(snapshot.products) & deleted_products or any(
                    set(operation.products or []) & deleted_products for operation in snapshot.operations.values()
                ):
                    raise HTTPException(400, "Cannot prune a product referenced by a retained API revision")
                if candidates["policy_fragments"]:
                    policies = [snapshot.policies_xml, *(op.policies_xml for op in snapshot.operations.values())]
                    if snapshot.graphql:
                        policies.extend(resolver.policies_xml for resolver in snapshot.graphql.resolvers.values())
                    for policy in policies:
                        if policy:
                            validate_fragment_references(policy, cfg.policy_fragments, cfg)
            except (ValueError, TypeError) as exc:
                raise HTTPException(400, "Cannot prune a fragment referenced by a retained API revision") from exc


def stage_service_artifact(manager: ManagementService, cfg: GatewayConfig, body: ServiceArtifactRequest):  # noqa: C901 - stages the service atomically
    artifact = body.artifact
    if body.prune and body.baseline is None:
        raise HTTPException(400, "Prune requires a baseline artifact")
    if body.baseline is not None and body.baseline.schema_version != 1:
        raise HTTPException(400, "Unsupported baseline artifact schema version")
    candidates = deletion_candidates(cfg, body)
    unlinks = unlink_candidates(cfg, body)
    if artifact.schema_version != 1:
        raise HTTPException(400, "Unsupported artifact schema version")
    if mask_secret_data(artifact.model_dump(), cfg) != artifact.model_dump():
        raise HTTPException(400, "Artifact contains a known secret")
    if cfg.routes and not cfg.apis:
        raise HTTPException(400, "Cannot import APIs into a legacy route-only configuration")
    for api_id, api in cfg.apis.items():
        if body.baseline is None and api_id not in artifact.apis and set(api.products) & set(artifact.products):
            raise HTTPException(400, "Removing product API links is not supported")
    staged = cfg.model_copy(deep=True)
    if body.prune:
        if candidates["apis"]:
            staged.routes = []
        for api_id, products in unlinks.items():
            staged.apis[api_id].products = [p for p in staged.apis[api_id].products if p not in products]
        for kind, ids in candidates.items():
            for identifier in ids:
                del getattr(staged, kind)[identifier]
    staged.policy_fragments.update(artifact.policy_fragments)
    staged.policies_xml = artifact.gateway_policy
    staged.policies_xml_documents = artifact.gateway_policy_documents
    _stage_dependencies(staged, artifact, body.overrides)
    _stage_products(staged, artifact.products)
    staged = _stage_apis(manager, staged, artifact, body.overrides, unlinks)
    if body.prune:
        for api_id in unlinks:

            def protected(config: GatewayConfig, identifier: str) -> bool:
                products = config.apis[identifier].products
                return bool(products) and all(
                    p not in config.products or config.products[p].require_subscription for p in products
                )

            previously_protected = protected(cfg, api_id)
            still_protected = protected(staged, api_id)
            if previously_protected and not still_protected:
                raise HTTPException(400, "Cannot unlink the last subscription-enforcing product from a retained API")
    if body.prune and any(candidates.values()):
        _check_prune_references(staged, candidates)
    policies = _policies(staged, artifact.apis, artifact.products, artifact.policy_fragments)
    actual_backends, actual_values = _dependencies(
        staged, policies, {entry.backend for entry in artifact.apis.values() if entry.backend}, allow_fragments=True
    )
    if set(actual_backends) != set(artifact.backends) or set(actual_values) != set(artifact.named_values):
        raise HTTPException(400, "Bundle dependencies do not match its policies and APIs")
    try:
        for policy in _policies(staged, artifact.apis, artifact.products, {}):
            if policy:
                validate_fragment_references(policy, artifact.policy_fragments, staged)
        for policy in _policies(staged, staged.apis, staged.products, {}):
            manager.validate_policy_xml(staged, policy)
        for fragment in staged.policy_fragments.values():
            manager.validate_fragment_xml(staged, fragment)
    except (HTTPException, ValueError, TypeError) as exc:
        if body.prune and candidates["policy_fragments"]:
            raise HTTPException(
                400, "Cannot prune policy fragments referenced by retained policies, or invalid policy configuration"
            ) from exc
        raise HTTPException(400, "Invalid policy or configuration in the service artifact") from exc

    from app.management_service import _validate_open_product_associations
    from app.security_governance import validate_governance_mutation

    validate_governance_mutation(manager.app.state.gateway_config, staged)
    try:
        _validate_open_product_associations(staged)
        manager._prepare_config(staged.model_copy(deep=True))
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "Invalid policy or configuration in the service artifact") from exc
    return staged, export_service_artifact(staged)


_MISSING = object()


def _change_category(previous: Any, wanted: Any, live: Any) -> str | None:
    git_changed = wanted != previous
    live_changed = live != previous
    if git_changed and live_changed:
        return "matching" if wanted == live else "conflicts"
    if git_changed:
        return "git_only"
    if live_changed:
        return "live_only"
    return None


def _normalized_product(value: Any) -> Any:
    if value is _MISSING:
        return value
    try:
        return ProductConfig.model_validate(value).model_dump(mode="json", exclude={"groups", "tags"})
    except ValidationError as exc:
        raise HTTPException(400, "Invalid baseline product metadata") from exc


def _api_comparison(value: Any, managed_products: set[str] | None = None) -> Any:
    if value is _MISSING:
        return value
    if managed_products is not None:
        value = {**value, "products": [product for product in value["products"] if product in managed_products]}
    paths = value.get("artifact", {}).get("openapi", {}).get("paths", {})
    if not isinstance(paths, dict):
        return value, None
    methods = {"get", "put", "post", "delete", "patch", "head", "options", "trace"}
    order = [
        (path, method)
        for path, item in paths.items()
        if isinstance(item, dict)
        for method in item
        if isinstance(method, str) and method.lower() in methods
    ]
    return value, order


def _identity(value: Any) -> Any:
    return value


def _reconciliation(current: dict, desired: dict, baseline: dict) -> dict:
    categories = ("git_only", "live_only", "matching", "conflicts")
    managed_products = set(baseline["products"]) | set(desired["products"])
    result = {}
    for kind in ("apis", "products", "policy_fragments"):
        groups = {category: [] for category in categories}
        for identifier in sorted(set(current[kind]) | set(desired[kind]) | set(baseline[kind])):
            values = [state[kind].get(identifier, _MISSING) for state in (baseline, desired, current)]
            if kind == "products":
                values = [_normalized_product(value) for value in values]
            elif kind == "apis":
                values = [_api_comparison(value, managed_products) for value in values]
            category = _change_category(*values)
            if category:
                groups[category].append(identifier)
        result[kind] = groups
    policies = [(state["gateway_policy"], state["gateway_policy_documents"]) for state in (baseline, desired, current)]
    result["gateway_policy"] = _change_category(*policies) or "unchanged"
    managed = set(baseline["apis"]) | set(desired["apis"])
    orders = [
        [identifier for identifier in state["apis"] if identifier in managed] for state in (baseline, desired, current)
    ]
    result["api_order"] = _change_category(*orders) or "unchanged"
    return result


def plan_service_artifact(manager: ManagementService, cfg: GatewayConfig, body: ServiceArtifactRequest):
    staged, proposed = stage_service_artifact(manager, cfg, body)
    current = export_service_artifact(cfg)

    def lines(value):
        return json.dumps(value, indent=2, ensure_ascii=False).splitlines()

    drift = {}
    baseline = body.baseline.model_dump(mode="json") if body.baseline else {}
    desired = body.artifact.model_dump(mode="json")
    for kind in ("apis", "products", "policy_fragments"):
        live, wanted, previous = current[kind], desired[kind], baseline.get(kind, {})
        compare = _api_comparison if kind == "apis" else _identity
        drift[kind] = {
            "create": sorted(set(wanted) - set(live)),
            "update": sorted(
                key for key in set(wanted) & set(live) if compare(proposed[kind][key]) != compare(live[key])
            ),
            "target_only": sorted(set(live) - set(wanted) - set(previous)),
        }
    drift["gateway_policy"] = (current["gateway_policy"], current["gateway_policy_documents"]) != (
        proposed["gateway_policy"],
        proposed["gateway_policy_documents"],
    )
    drift["api_order"] = list(current["apis"]) != list(proposed["apis"])
    drift["overrides"] = {
        "api_upstreams": sorted(
            key
            for key in body.overrides.api_upstreams
            if key not in cfg.apis or cfg.apis[key].upstream_base_url != staged.apis[key].upstream_base_url
        ),
        "backends": sorted(
            key
            for key in body.overrides.backends
            if key not in cfg.backends or cfg.backends[key].url != staged.backends[key].url
        ),
        "named_values": sorted(
            key for key in body.overrides.named_values if cfg.named_values.get(key) != staged.named_values[key]
        ),
    }
    result = {
        "etag": reviewed_etag(cfg, body) if body.prune or body.reviewed else service_etag(cfg),
        "drift": drift,
        "deletions": deletion_candidates(cfg, body),
        "unlinks": unlink_candidates(cfg, body),
        "prune": body.prune,
        "reviewed": body.reviewed,
        "diff": "\n".join(
            difflib.unified_diff(lines(current), lines(proposed), fromfile="current", tofile="proposed", lineterm="")
        ),
        "overrides": {key: sorted(value) for key, value in body.overrides.model_dump().items()},
    }
    if body.baseline is not None:
        result["reconciliation"] = _reconciliation(current, desired, baseline)
    return result
