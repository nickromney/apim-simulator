"""Portable product bundles with target-local dependency overrides."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from typing import Any

from defusedxml import ElementTree
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.api_artifacts import ApiArtifact, ApiArtifactRequest, _validate_upstream, export_artifact, stage_artifact
from app.config import BackendConfig, GatewayConfig, NamedValueConfig, ProductConfig
from app.named_values import NAMED_VALUE_PATTERN, _find_named_value, mask_secret_data


class ProductApiArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact: ApiArtifact
    backend: str | None


class NamedValueMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str
    secret: bool


class ProductArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int
    product: dict[str, Any]
    apis: dict[str, ProductApiArtifact]
    backends: list[str]
    named_values: dict[str, NamedValueMetadata]


class ProductOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_upstreams: dict[str, str] = Field(default_factory=dict)
    backends: dict[str, str] = Field(default_factory=dict)
    named_values: dict[str, str] = Field(default_factory=dict)


class ProductArtifactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact: ProductArtifact
    overrides: ProductOverrides = Field(default_factory=ProductOverrides)


def product_etag(cfg: GatewayConfig, product_id: str) -> str:
    value = cfg.model_dump(mode="json", exclude={"routes"})
    raw = json.dumps([product_id, value], sort_keys=False, separators=(",", ":"))
    return f'"{hashlib.sha256(raw.encode()).hexdigest()}"'


def _dependencies(  # noqa: C901 - checks each policy dependency kind
    cfg: GatewayConfig, policies: list[str | None], api_backends: set[str], *, allow_fragments: bool = False
) -> tuple[list[str], dict]:
    backends = set(api_backends)
    named_values = {}
    for policy in filter(None, policies):
        for match in NAMED_VALUE_PATTERN.finditer(policy):
            identifier, value = _find_named_value(cfg, match.group(1).strip())
            if value is None:
                raise HTTPException(400, "Policy references an unknown named value")
            named_values[identifier] = {
                "display_name": value.display_name or identifier,
                "secret": value.secret or value.value_from_key_vault is not None,
            }
        try:
            root = ElementTree.fromstring(policy)
        except Exception as exc:
            raise HTTPException(400, "Invalid policy XML") from exc
        for element in root.iter():
            if element.tag.split("}")[-1] == "include-fragment" and not allow_fragments:
                raise HTTPException(
                    400, "Policy fragments are not supported in product artifacts; inline the policy first"
                )
            backend = element.attrib.get("backend-id")
            if backend and re.search(r"[{}@]", backend):
                raise HTTPException(400, "Dynamic backend-id cannot be represented in product artifacts")
            if backend:
                backends.add(backend)
    return sorted(backends), dict(sorted(named_values.items()))


def export_product_artifact(cfg: GatewayConfig, product_id: str) -> dict[str, Any]:  # noqa: C901 - checks portable membership and routing
    product = cfg.products.get(product_id)
    if product is None:
        raise HTTPException(404, "Product not found")
    apis = {}
    policies = [product.policies_xml]
    for api_id, api in cfg.apis.items():
        if any(product_id in (op.products or []) for op in api.operations.values()):
            raise HTTPException(
                400, "Operation-level product membership cannot be represented; move membership to the API"
            )
        if product_id not in api.products:
            continue
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
                raise HTTPException(
                    400, "Operation-level backend settings cannot be represented; move routing to the API"
                )
        apis[api_id] = {"artifact": export_artifact(cfg, api_id), "backend": api.backend}
        policies.extend([api.policies_xml, *(op.policies_xml for op in api.operations.values())])
    backends, named_values = _dependencies(cfg, policies, {a["backend"] for a in apis.values() if a["backend"]})
    result = {
        "schema_version": 1,
        "product": product.model_dump(mode="json", exclude={"groups", "tags"}),
        "apis": apis,
        "backends": backends,
        "named_values": named_values,
    }
    if mask_secret_data(result, cfg) != result:
        raise HTTPException(400, "Artifact contains a known secret; replace it with a named value reference")
    return result


def _stage_dependencies(cfg: GatewayConfig, artifact: ProductArtifact, overrides: ProductOverrides) -> None:  # noqa: C901 - validates each dependency type
    if (
        set(overrides.api_upstreams) - set(artifact.apis)
        or set(overrides.backends) - set(artifact.backends)
        or set(overrides.named_values) - set(artifact.named_values)
    ):
        raise HTTPException(400, "Overrides must refer to bundle dependencies")
    if any(artifact.apis[identifier].backend for identifier in overrides.api_upstreams):
        raise HTTPException(400, "Use a backend override for APIs that reference a named backend")
    for identifier in artifact.backends:
        url = overrides.backends.get(identifier)
        if url is not None:
            _validate_upstream(url)
            if not url:
                raise HTTPException(400, "Backend override must be a URL")
            if identifier in cfg.backends:
                if cfg.backends[identifier].type == "pool":
                    raise HTTPException(400, f"Backend {identifier} is a pool; configure its members on the target")
                cfg.backends[identifier].url = url
            else:
                cfg.backends[identifier] = BackendConfig(url=url)
        elif identifier not in cfg.backends:
            raise HTTPException(400, f"Backend {identifier} requires an override")
    for identifier, metadata in artifact.named_values.items():
        value = overrides.named_values.get(identifier)
        existing = cfg.named_values.get(identifier)
        resolved_id, resolved = _find_named_value(cfg, metadata.display_name)
        if resolved is not None and resolved_id != identifier:
            raise HTTPException(400, f"Named value {identifier} display name conflicts with another target named value")
        if existing is not None and (existing.display_name or identifier) != metadata.display_name:
            raise HTTPException(400, f"Named value {identifier} has a different target display name")
        if existing is None:
            if value is None:
                raise HTTPException(400, f"Named value {identifier} requires an override")
            cfg.named_values[identifier] = NamedValueConfig(
                display_name=metadata.display_name, secret=metadata.secret, value=value
            )
        elif value is None and metadata.secret and not (existing.secret or existing.value_from_key_vault is not None):
            raise HTTPException(
                400, f"Named value {identifier} must be secret on the target or supplied as an override"
            )
        elif value is not None:
            existing.secret = existing.secret or existing.value_from_key_vault is not None or metadata.secret
            existing.value = value
            existing.value_from_key_vault = None


def stage_product_artifact(manager, cfg: GatewayConfig, product_id: str, body: ProductArtifactRequest):  # noqa: C901 - stages the complete bundle before validation
    artifact = body.artifact
    if artifact.schema_version != 1:
        raise HTTPException(400, "Unsupported artifact schema version")
    if mask_secret_data(artifact.model_dump(), cfg) != artifact.model_dump():
        raise HTTPException(400, "Artifact contains a known secret")
    if any(product_id in api.products and api_id not in artifact.apis for api_id, api in cfg.apis.items()):
        raise HTTPException(400, "Removing product API links is not supported; include all currently linked APIs")
    if any(product_id in (op.products or []) for api in cfg.apis.values() for op in api.operations.values()):
        raise HTTPException(400, "Operation-level product membership cannot be represented; move membership to the API")
    staged = cfg.model_copy(deep=True)
    _stage_dependencies(staged, artifact, body.overrides)
    existing = staged.products.get(product_id)
    portable_fields = set(ProductConfig.model_fields) - {"groups", "tags"}
    if not {"name", "state", "require_subscription"} <= artifact.product.keys() <= portable_fields:
        raise HTTPException(400, "Invalid product artifact metadata")
    try:
        staged.products[product_id] = ProductConfig.model_validate(
            {
                **artifact.product,
                "groups": existing.groups if existing else [],
                "tags": existing.tags if existing else [],
            }
        )
    except ValidationError as exc:
        raise HTTPException(400, "Invalid product artifact metadata") from exc
    for api_id, entry in artifact.apis.items():
        target_api = staged.apis.get(api_id)
        if target_api and any(op.products is not None for op in target_api.operations.values()):
            raise HTTPException(400, "Target API has operation-level product membership; move membership to the API")
        upstream = body.overrides.api_upstreams.get(api_id)
        if entry.backend is None and target_api and target_api.backend and upstream is None:
            raise HTTPException(400, "An API switching to direct routing requires an upstream override")
        staged, _ = stage_artifact(
            manager,
            staged,
            api_id,
            ApiArtifactRequest(artifact=entry.artifact, upstream_base_url=upstream),
            defer_validation=True,
            backend=entry.backend,
        )
        api = staged.apis[api_id]
        api.backend = entry.backend
        if product_id not in api.products:
            api.products.append(product_id)
    policies = [staged.products[product_id].policies_xml]
    for api_id in artifact.apis:
        api = staged.apis[api_id]
        policies.extend([api.policies_xml, *(op.policies_xml for op in api.operations.values())])
    actual_backends, actual_values = _dependencies(
        staged, policies, {entry.backend for entry in artifact.apis.values() if entry.backend}
    )
    if set(actual_backends) != set(artifact.backends) or set(actual_values) != set(artifact.named_values):
        raise HTTPException(400, "Bundle dependencies do not match its policies and APIs")
    try:
        for policy in policies:
            manager.validate_policy_xml(staged, policy)
    except HTTPException as exc:
        raise HTTPException(400, "Invalid policy or named value override in the product artifact") from exc
    try:
        from app.management_service import _validate_open_product_associations
        from app.security_governance import validate_governance_mutation

        validate_governance_mutation(manager.app.state.gateway_config, staged)
        _validate_open_product_associations(staged)
        manager._prepare_config(staged.model_copy(deep=True))
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "Invalid config update") from exc
    return staged, export_product_artifact(staged, product_id)


def plan_product_artifact(manager, cfg: GatewayConfig, product_id: str, body: ProductArtifactRequest):
    _, proposed = stage_product_artifact(manager, cfg, product_id, body)
    current = export_product_artifact(cfg, product_id) if product_id in cfg.products else None

    def lines(value):
        return json.dumps(value, indent=2, ensure_ascii=False).splitlines()

    return {
        "product_id": product_id,
        "etag": product_etag(cfg, product_id),
        "diff": "\n".join(
            difflib.unified_diff(lines(current), lines(proposed), fromfile="current", tofile="proposed", lineterm="")
        ),
        "overrides": {key: sorted(value) for key, value in body.overrides.model_dump().items()},
    }
