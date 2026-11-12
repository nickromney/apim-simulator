"""Portable service configuration with target-local dependencies."""

from __future__ import annotations

import difflib
import hashlib
import json
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
    manager: ManagementService, cfg: GatewayConfig, artifact: ServiceArtifact, overrides: ProductOverrides
) -> GatewayConfig:
    for api_id, entry in artifact.apis.items():
        target = cfg.apis.get(api_id)
        if len(entry.products) != len(set(entry.products)) or set(entry.products) - set(artifact.products):
            raise HTTPException(400, "API product links must reference bundle products")
        if target:
            _api_portable(target)
            if (set(target.products) & set(artifact.products)) - set(entry.products):
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


def stage_service_artifact(manager: ManagementService, cfg: GatewayConfig, body: ServiceArtifactRequest):  # noqa: C901 - stages the service atomically
    artifact = body.artifact
    if artifact.schema_version != 1:
        raise HTTPException(400, "Unsupported artifact schema version")
    if mask_secret_data(artifact.model_dump(), cfg) != artifact.model_dump():
        raise HTTPException(400, "Artifact contains a known secret")
    if cfg.routes and not cfg.apis:
        raise HTTPException(400, "Cannot import APIs into a legacy route-only configuration")
    for api_id, api in cfg.apis.items():
        if api_id not in artifact.apis and set(api.products) & set(artifact.products):
            raise HTTPException(400, "Removing product API links is not supported")
    staged = cfg.model_copy(deep=True)
    staged.policy_fragments.update(artifact.policy_fragments)
    staged.policies_xml = artifact.gateway_policy
    staged.policies_xml_documents = artifact.gateway_policy_documents
    _stage_dependencies(staged, artifact, body.overrides)
    _stage_products(staged, artifact.products)
    staged = _stage_apis(manager, staged, artifact, body.overrides)
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


def plan_service_artifact(manager: ManagementService, cfg: GatewayConfig, body: ServiceArtifactRequest):
    _, proposed = stage_service_artifact(manager, cfg, body)
    current = export_service_artifact(cfg)

    def lines(value):
        return json.dumps(value, indent=2, ensure_ascii=False).splitlines()

    return {
        "etag": service_etag(cfg),
        "diff": "\n".join(
            difflib.unified_diff(lines(current), lines(proposed), fromfile="current", tofile="proposed", lineterm="")
        ),
        "overrides": {key: sorted(value) for key, value in body.overrides.model_dump().items()},
    }
