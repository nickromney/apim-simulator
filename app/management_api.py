"""Management-plane REST surface (/apim/management/*) and developer portal routes.

Extracted from the ``create_app`` closure in ``app.main``. Route paths and
behaviour are pinned by ``tests/test_app_composition.py``; registration order
relative to the gateway catch-all route is preserved by including this router
at the same position the inline routes previously occupied.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable
from copy import deepcopy
from typing import Any

import httpx
from defusedxml import ElementTree
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.config import (
    ApiConfig,
    ApiReleaseConfig,
    ApiRevisionConfig,
    ApiSchemaConfig,
    ApiVersioningScheme,
    ApiVersionSetConfig,
    BackendConfig,
    DiagnosticConfig,
    GatewayConfig,
    GroupConfig,
    KeyVaultNamedValueConfig,
    LoggerConfig,
    NamedValueConfig,
    OperationConfig,
    OperationParameterConfig,
    OperationRequestMetadataConfig,
    OperationResponseMetadataConfig,
    ProductConfig,
    ProductState,
    RouteAuthzConfig,
    SubscriptionState,
    TagConfig,
    UserConfig,
)
from app.management_service import ManagementService
from app.named_values import mask_secret_data
from app.openapi_import import parse_api_import
from app.policy import (
    parse_policies_xml,
)
from app.portal import (
    PORTAL_HTML,
    create_portal_subscription,
    portal_catalog,
    portal_subscriptions,
    portal_users,
    project_portal_subscription,
    require_portal_user,
)
from app.request_auth import (
    _find_subscription_entry,
    _require_tenant_access,
)
from app.resource_projection import (
    project_api,
    project_api_release,
    project_api_revision,
    project_api_schema,
    project_api_tag_link,
    project_api_version_set,
    project_backend,
    project_diagnostic,
    project_group,
    project_group_user_link,
    project_logger,
    project_named_value,
    project_operation,
    project_operation_tag_link,
    project_policy_fragment,
    project_product,
    project_product_group_link,
    project_product_tag_link,
    project_service,
    project_subscription,
    project_summary,
    project_tag,
    project_user,
)
from app.terraform_import import import_from_tofu_show_json

logger = logging.getLogger("apim-simulator")

EMPTY_POLICY_XML = "<policies><inbound /><backend /><outbound /><on-error /></policies>"
POLICY_SECTION_NAMES = ("inbound", "backend", "outbound", "on-error")


def _merge_policy_xml_documents(xml_documents: list[str]) -> str:
    if not xml_documents:
        return EMPTY_POLICY_XML
    if len(xml_documents) == 1:
        return xml_documents[0]

    root = ElementTree.Element("policies")
    sections = {name: ElementTree.SubElement(root, name) for name in POLICY_SECTION_NAMES}

    for xml in xml_documents:
        try:
            parsed = ElementTree.fromstring(xml)
        except ElementTree.ParseError:
            continue
        if parsed.tag != "policies":
            continue
        for section_name in POLICY_SECTION_NAMES:
            source = parsed.find(section_name)
            if source is None:
                continue
            for child in list(source):
                sections[section_name].append(deepcopy(child))

    return ElementTree.tostring(root, encoding="unicode")


def _effective_policy_xml(*groups: list[str] | None) -> str:
    xml_documents: list[str] = []
    for group in groups:
        if not group:
            continue
        xml_documents.extend(item for item in group if item)
    return _merge_policy_xml_documents(xml_documents)


def _decode_body(content: bytes) -> dict[str, str | None]:
    if not content:
        return {"text": "", "base64": None}
    try:
        return {"text": content.decode("utf-8"), "base64": None}
    except UnicodeDecodeError:
        return {"text": None, "base64": base64.b64encode(content).decode("ascii")}


class SubscriptionUpsert(BaseModel):
    id: str
    name: str
    state: SubscriptionState = SubscriptionState.Active
    products: list[str] = Field(default_factory=list)
    primary_key: str | None = None
    secondary_key: str | None = None


class SubscriptionUpdate(BaseModel):
    name: str | None = None
    state: SubscriptionState | None = None
    products: list[str] | None = None


class ApiUpsert(BaseModel):
    name: str | None = None
    path: str
    upstream_base_url: str
    upstream_path_prefix: str = ""
    backend: str | None = None
    products: list[str] = Field(default_factory=list)
    api_version_set: str | None = None
    api_version: str | None = None
    subscription_header_names: list[str] | None = None
    subscription_query_param_names: list[str] | None = None
    policies_xml: str | None = None


class OperationUpsert(BaseModel):
    name: str | None = None
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
    tags: list[str] | None = None
    template_parameters: list[OperationParameterConfig] | None = None
    request: OperationRequestMetadataConfig | None = None
    responses: list[OperationResponseMetadataConfig] | None = None


class ApiImportRequest(BaseModel):
    name: str | None = None
    path: str | None = None
    content_format: str
    content_value: str
    upstream_base_url: str | None = None
    upstream_path_prefix: str = ""
    backend: str | None = None
    products: list[str] | None = None
    api_version_set: str | None = None
    api_version: str | None = None
    subscription_header_names: list[str] | None = None
    subscription_query_param_names: list[str] | None = None
    policies_xml: str | None = None


class ApiVersionSetUpsert(BaseModel):
    display_name: str
    description: str | None = None
    versioning_scheme: str
    version_header_name: str | None = None
    version_query_name: str | None = None
    default_version: str | None = None


class ApiRevisionUpsert(BaseModel):
    description: str | None = None
    is_current: bool | None = None
    is_online: bool | None = None
    source_api_id: str | None = None


class ApiReleaseUpsert(BaseModel):
    name: str | None = None
    api_id: str | None = None
    notes: str | None = None
    revision: str


class ProductUpsert(BaseModel):
    name: str
    description: str | None = None
    state: ProductState = ProductState.Published
    require_subscription: bool = True
    approval_required: bool = False


class PortalSubscriptionRequest(BaseModel):
    product_id: str
    name: str | None = None


class GroupUpsert(BaseModel):
    name: str
    description: str | None = None
    external_id: str | None = None
    type: str = "custom"


class UserUpsert(BaseModel):
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    note: str | None = None
    state: str | None = None
    confirmation: str | None = None


class TagUpsert(BaseModel):
    display_name: str | None = None


class BackendUpsert(BaseModel):
    url: str
    description: str | None = None
    auth_type: str = "none"
    basic_username: str | None = None
    basic_password: str | None = None
    managed_identity_resource: str | None = None
    authorization_scheme: str | None = None
    authorization_parameter: str | None = None
    header_credentials: dict[str, str] = Field(default_factory=dict)
    query_credentials: dict[str, str] = Field(default_factory=dict)
    client_certificate_thumbprints: list[str] = Field(default_factory=list)


class NamedValueUpsert(BaseModel):
    value: str | None = None
    secret: bool = False
    value_from_key_vault: KeyVaultNamedValueConfig | None = None


class PolicyUpdate(BaseModel):
    xml: str


class PolicyFragmentUpsert(BaseModel):
    xml: str


class ReplayRequestBody(BaseModel):
    method: str = "GET"
    path: str
    query: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body_text: str | None = None
    body_base64: str | None = None


OperationUpsert.model_rebuild()


def build_management_router(*, require_management_plane: Callable[[], ManagementService]) -> APIRouter:
    router = APIRouter()

    def _persist_or_apply_config(request: Request, cfg: GatewayConfig) -> GatewayConfig:
        return require_management_plane().persist_or_apply_config(cfg)

    def _policy_scope_target(cfg: GatewayConfig, scope_type: str, scope_name: str) -> Any:
        scope = scope_type.lower()
        if scope == "gateway":
            return cfg
        if scope == "api":
            api = cfg.apis.get(scope_name)
            if api is None:
                raise HTTPException(status_code=404, detail="API policy scope not found")
            return api
        if scope == "product":
            product = cfg.products.get(scope_name)
            if product is None:
                raise HTTPException(status_code=404, detail="Product policy scope not found")
            return product
        if scope == "operation":
            api_name, sep, operation_name = scope_name.partition(":")
            if not sep:
                raise HTTPException(status_code=400, detail="Operation scope must use api:operation")
            api = cfg.apis.get(api_name)
            if api is None:
                raise HTTPException(status_code=404, detail="API policy scope not found")
            operation = api.operations.get(operation_name)
            if operation is None:
                raise HTTPException(status_code=404, detail="Operation policy scope not found")
            return operation
        if scope == "route":
            if cfg.apis:
                raise HTTPException(
                    status_code=400, detail="Route policy updates are unavailable for API-backed configs"
                )
            for route in cfg.routes:
                if route.name == scope_name:
                    return route
            raise HTTPException(status_code=404, detail="Route policy scope not found")
        raise HTTPException(status_code=404, detail="Unsupported policy scope")

    def _policy_xml_for_target(target: Any) -> str:
        docs = list(getattr(target, "policies_xml_documents", []) or [])
        xml = getattr(target, "policies_xml", None)
        if xml:
            docs.append(xml)
        return _effective_policy_xml(docs)

    def _set_policy_xml(target: Any, xml: str) -> None:
        target.policies_xml = xml
        if hasattr(target, "policies_xml_documents"):
            target.policies_xml_documents = []

    def _masked(cfg: GatewayConfig, payload: Any) -> Any:
        return mask_secret_data(payload, cfg)

    def _summary_payload(cfg: GatewayConfig, request: Request | None = None) -> dict[str, Any]:
        trace_store = getattr(request.app.state, "trace_store", None) if request is not None else None
        return project_summary(cfg, trace_store=trace_store)

    def _ensure_api_authoring_mode(cfg: GatewayConfig) -> None:
        if not cfg.apis and cfg.routes:
            raise HTTPException(
                status_code=400,
                detail="API CRUD requires api-authored config; convert legacy route configs before mutating APIs.",
            )

    def _get_api_or_404(cfg: GatewayConfig, api_id: str) -> ApiConfig:
        api = cfg.apis.get(api_id)
        if api is None:
            raise HTTPException(status_code=404, detail="API not found")
        return api

    def _get_operation_or_404(cfg: GatewayConfig, api_id: str, operation_id: str) -> OperationConfig:
        api = _get_api_or_404(cfg, api_id)
        operation = api.operations.get(operation_id)
        if operation is None:
            raise HTTPException(status_code=404, detail="Operation not found")
        return operation

    def _get_api_schema_or_404(cfg: GatewayConfig, api_id: str, schema_id: str) -> ApiSchemaConfig:
        api = _get_api_or_404(cfg, api_id)
        schema = api.schemas.get(schema_id)
        if schema is None:
            raise HTTPException(status_code=404, detail="API schema not found")
        return schema

    def _get_api_revision_or_404(cfg: GatewayConfig, api_id: str, revision_id: str) -> ApiRevisionConfig:
        api = _get_api_or_404(cfg, api_id)
        revision = api.revisions.get(revision_id)
        if revision is None:
            raise HTTPException(status_code=404, detail="API revision not found")
        return revision

    def _get_api_release_or_404(cfg: GatewayConfig, api_id: str, release_id: str) -> ApiReleaseConfig:
        api = _get_api_or_404(cfg, api_id)
        release = api.releases.get(release_id)
        if release is None:
            raise HTTPException(status_code=404, detail="API release not found")
        return release

    def _get_product_or_404(cfg: GatewayConfig, product_id: str) -> ProductConfig:
        product = cfg.products.get(product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="Product not found")
        return product

    def _get_group_or_404(cfg: GatewayConfig, group_id: str) -> GroupConfig:
        group = cfg.groups.get(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="Group not found")
        return group

    def _get_user_or_404(cfg: GatewayConfig, user_id: str) -> UserConfig:
        user = cfg.users.get(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        return user

    def _get_tag_or_404(cfg: GatewayConfig, tag_id: str) -> TagConfig:
        tag = cfg.tags.get(tag_id)
        if tag is None:
            raise HTTPException(status_code=404, detail="Tag not found")
        return tag

    def _get_backend_or_404(cfg: GatewayConfig, backend_id: str) -> BackendConfig:
        backend = cfg.backends.get(backend_id)
        if backend is None:
            raise HTTPException(status_code=404, detail="Backend not found")
        return backend

    def _get_logger_or_404(cfg: GatewayConfig, logger_id: str) -> LoggerConfig:
        logger_entry = cfg.loggers.get(logger_id)
        if logger_entry is None:
            raise HTTPException(status_code=404, detail="Logger not found")
        return logger_entry

    def _get_diagnostic_or_404(cfg: GatewayConfig, diagnostic_id: str) -> DiagnosticConfig:
        diagnostic = cfg.diagnostics.get(diagnostic_id)
        if diagnostic is None:
            raise HTTPException(status_code=404, detail="Diagnostic not found")
        return diagnostic

    def _get_named_value_or_404(cfg: GatewayConfig, named_value_id: str) -> NamedValueConfig:
        named_value = cfg.named_values.get(named_value_id)
        if named_value is None:
            raise HTTPException(status_code=404, detail="Named value not found")
        return named_value

    def _validate_fragment_xml(xml: str) -> None:
        try:
            ElementTree.fromstring(f"<fragment>{xml}</fragment>")
        except ElementTree.ParseError as exc:
            raise HTTPException(status_code=400, detail="Invalid policy fragment XML") from exc

    def _validate_policy_xml(cfg: GatewayConfig, xml: str | None) -> None:
        if xml is None:
            return
        parse_policies_xml(xml.strip() or EMPTY_POLICY_XML, policy_fragments=cfg.policy_fragments)

    def _coerce_api_versioning_scheme(raw: str) -> ApiVersioningScheme:
        normalized = (raw or "").strip().lower()
        mapping = {
            "header": ApiVersioningScheme.Header,
            "query": ApiVersioningScheme.Query,
            "segment": ApiVersioningScheme.Segment,
        }
        scheme = mapping.get(normalized)
        if scheme is None:
            raise HTTPException(status_code=400, detail="Unsupported API versioning scheme")
        return scheme

    def _default_release_api_id(cfg: GatewayConfig, api_id: str, revision_id: str) -> str:
        return f"service/{cfg.service.name}/apis/{api_id};rev={revision_id}"

    def _set_current_revision(api: ApiConfig, revision_id: str, revision: ApiRevisionConfig) -> None:
        for candidate_id, candidate in api.revisions.items():
            candidate.is_current = candidate_id == revision_id
        api.revision = revision_id
        api.revision_description = revision.description
        api.source_api_id = revision.source_api_id
        api.is_current = True
        api.is_online = revision.is_online

    def _link_list_item(values: list[str], item_id: str) -> bool:
        if item_id in values:
            return False
        values.append(item_id)
        return True

    def _unlink_list_item(values: list[str], item_id: str) -> bool:
        if item_id not in values:
            return False
        values[:] = [item for item in values if item != item_id]
        return True

    @router.get("/apim/management/status")
    async def management_status(request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        service = project_service(cfg, trace_store=request.app.state.trace_store)
        return {
            "service": {
                "id": service["id"],
                "name": service["name"],
                "display_name": service["display_name"],
            },
            "counts": service["counts"],
            "gateway_policy_scope": service["gateway_policy_scope"],
        }

    @router.get("/apim/management/service")
    async def management_service(request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        return _masked(cfg, project_service(cfg, trace_store=request.app.state.trace_store))

    @router.get("/apim/management/summary")
    async def management_summary(request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        return _summary_payload(cfg, request)

    @router.get("/apim/management/apis")
    async def list_apis(request: Request) -> list[dict[str, Any]]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        return [_masked(cfg, project_api(cfg, api_id, api)) for api_id, api in cfg.apis.items()]

    @router.get("/apim/management/apis/{api_id}")
    async def get_api(api_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        api = _get_api_or_404(cfg, api_id)
        return _masked(cfg, project_api(cfg, api_id, api))

    @router.post("/apim/management/apis/{api_id}/import")
    async def import_api(api_id: str, request: Request, body: ApiImportRequest) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        _ensure_api_authoring_mode(cfg)
        _validate_policy_xml(cfg, body.policies_xml)

        try:
            imported = parse_api_import(content_format=body.content_format, content_value=body.content_value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        existing = cfg.apis.get(api_id)
        upstream_base_url = body.upstream_base_url or imported.upstream_base_url
        if not upstream_base_url and existing is not None:
            upstream_base_url = existing.upstream_base_url
        if not upstream_base_url:
            raise HTTPException(
                status_code=400,
                detail="Imported API is missing an upstream base URL; provide upstream_base_url explicitly.",
            )

        operations: dict[str, OperationConfig] = {}
        existing_operations = existing.operations if existing is not None else {}
        for imported_operation in imported.operations:
            preserved = existing_operations.get(imported_operation.name)
            operations[imported_operation.name] = OperationConfig(
                name=preserved.name if preserved is not None else imported_operation.name,
                method=imported_operation.method,
                url_template=imported_operation.url_template,
                description=preserved.description if preserved is not None else None,
                upstream_base_url=preserved.upstream_base_url if preserved is not None else None,
                upstream_path_prefix=preserved.upstream_path_prefix if preserved is not None else None,
                backend=preserved.backend if preserved is not None else None,
                products=preserved.products if preserved is not None else None,
                api_version_set=preserved.api_version_set if preserved is not None else None,
                api_version=preserved.api_version if preserved is not None else None,
                subscription_header_names=preserved.subscription_header_names if preserved is not None else None,
                subscription_query_param_names=(
                    preserved.subscription_query_param_names if preserved is not None else None
                ),
                authz=preserved.authz if preserved is not None else None,
                policies_xml=preserved.policies_xml if preserved is not None else None,
                tags=preserved.tags if preserved is not None else [],
                template_parameters=preserved.template_parameters if preserved is not None else [],
                request=preserved.request if preserved is not None else None,
                responses=preserved.responses if preserved is not None else [],
            )

        cfg.apis[api_id] = ApiConfig(
            name=body.name or (existing.name if existing is not None else api_id),
            path=body.path or (existing.path if existing is not None else api_id),
            upstream_base_url=upstream_base_url,
            upstream_path_prefix=body.upstream_path_prefix,
            backend=body.backend if body.backend is not None else (existing.backend if existing is not None else None),
            products=body.products
            if body.products is not None
            else (existing.products if existing is not None else []),
            api_version_set=(
                body.api_version_set
                if body.api_version_set is not None
                else (existing.api_version_set if existing else None)
            ),
            api_version=body.api_version
            if body.api_version is not None
            else (existing.api_version if existing else None),
            revision=existing.revision if existing is not None else None,
            revision_description=existing.revision_description if existing is not None else None,
            version_description=existing.version_description if existing is not None else None,
            source_api_id=existing.source_api_id if existing is not None else None,
            is_current=existing.is_current if existing is not None else None,
            is_online=existing.is_online if existing is not None else None,
            subscription_header_names=(
                body.subscription_header_names
                if body.subscription_header_names is not None
                else (existing.subscription_header_names if existing else None)
            ),
            subscription_query_param_names=(
                body.subscription_query_param_names
                if body.subscription_query_param_names is not None
                else (existing.subscription_query_param_names if existing else None)
            ),
            policies_xml=body.policies_xml
            if body.policies_xml is not None
            else (existing.policies_xml if existing else None),
            tags=existing.tags if existing is not None else [],
            operations=operations,
            schemas=existing.schemas if existing is not None else {},
            revisions=existing.revisions if existing is not None else {},
            releases=existing.releases if existing is not None else {},
        )
        updated = _persist_or_apply_config(request, cfg)
        api = _get_api_or_404(updated, api_id)
        return {
            "api": _masked(updated, project_api(updated, api_id, api)),
            "import": {
                "format": imported.format,
                "operation_count": len(imported.operations),
                "upstream_base_url": imported.upstream_base_url,
                "diagnostics": imported.diagnostics,
            },
        }

    @router.put("/apim/management/apis/{api_id}")
    async def upsert_api(api_id: str, request: Request, body: ApiUpsert) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        _ensure_api_authoring_mode(cfg)
        _validate_policy_xml(cfg, body.policies_xml)
        existing = cfg.apis.get(api_id)
        cfg.apis[api_id] = ApiConfig(
            name=body.name or api_id,
            path=body.path,
            upstream_base_url=body.upstream_base_url,
            upstream_path_prefix=body.upstream_path_prefix,
            backend=body.backend,
            products=body.products,
            api_version_set=body.api_version_set,
            api_version=body.api_version,
            revision=existing.revision if existing is not None else None,
            revision_description=existing.revision_description if existing is not None else None,
            version_description=existing.version_description if existing is not None else None,
            source_api_id=existing.source_api_id if existing is not None else None,
            is_current=existing.is_current if existing is not None else None,
            is_online=existing.is_online if existing is not None else None,
            subscription_header_names=body.subscription_header_names,
            subscription_query_param_names=body.subscription_query_param_names,
            policies_xml=body.policies_xml,
            tags=existing.tags if existing is not None else [],
            operations=existing.operations if existing is not None else {},
            schemas=existing.schemas if existing is not None else {},
            revisions=existing.revisions if existing is not None else {},
            releases=existing.releases if existing is not None else {},
        )
        updated = _persist_or_apply_config(request, cfg)
        api = _get_api_or_404(updated, api_id)
        return _masked(updated, project_api(updated, api_id, api))

    @router.delete("/apim/management/apis/{api_id}")
    async def delete_api(api_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        _ensure_api_authoring_mode(cfg)
        _get_api_or_404(cfg, api_id)
        del cfg.apis[api_id]
        updated = _persist_or_apply_config(request, cfg)
        return {"deleted": True, "api_id": api_id, "remaining": len(updated.apis)}

    @router.get("/apim/management/operations")
    async def list_operations(request: Request) -> list[dict[str, Any]]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        operations: list[dict[str, Any]] = []
        for api_id, api in cfg.apis.items():
            for operation_id, operation in api.operations.items():
                operations.append(_masked(cfg, project_operation(cfg, api_id, operation_id, operation)))
        return operations

    @router.get("/apim/management/apis/{api_id}/operations")
    async def list_api_operations(api_id: str, request: Request) -> list[dict[str, Any]]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        api = _get_api_or_404(cfg, api_id)
        return [
            _masked(cfg, project_operation(cfg, api_id, operation_id, operation))
            for operation_id, operation in api.operations.items()
        ]

    @router.get("/apim/management/apis/{api_id}/schemas")
    async def list_api_schemas(api_id: str, request: Request) -> list[dict[str, Any]]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        api = _get_api_or_404(cfg, api_id)
        return [
            _masked(cfg, project_api_schema(cfg, api_id, schema_id, schema))
            for schema_id, schema in api.schemas.items()
        ]

    @router.get("/apim/management/apis/{api_id}/revisions")
    async def list_api_revisions(api_id: str, request: Request) -> list[dict[str, Any]]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        api = _get_api_or_404(cfg, api_id)
        return [
            _masked(cfg, project_api_revision(cfg, api_id, revision_id, revision))
            for revision_id, revision in api.revisions.items()
        ]

    @router.get("/apim/management/apis/{api_id}/revisions/{revision_id}")
    async def get_api_revision(api_id: str, revision_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        revision = _get_api_revision_or_404(cfg, api_id, revision_id)
        return _masked(cfg, project_api_revision(cfg, api_id, revision_id, revision))

    @router.put("/apim/management/apis/{api_id}/revisions/{revision_id}")
    async def upsert_api_revision(
        api_id: str, revision_id: str, request: Request, body: ApiRevisionUpsert
    ) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        _ensure_api_authoring_mode(cfg)
        api = _get_api_or_404(cfg, api_id)
        existing = api.revisions.get(revision_id)
        revision = ApiRevisionConfig(
            revision=revision_id,
            description=body.description
            if body.description is not None
            else (existing.description if existing else None),
            is_current=body.is_current if body.is_current is not None else (existing.is_current if existing else None),
            is_online=body.is_online if body.is_online is not None else (existing.is_online if existing else None),
            source_api_id=(
                body.source_api_id if body.source_api_id is not None else (existing.source_api_id if existing else None)
            ),
        )
        api.revisions[revision_id] = revision
        if revision.is_current:
            _set_current_revision(api, revision_id, revision)
        updated = _persist_or_apply_config(request, cfg)
        stored = _get_api_revision_or_404(updated, api_id, revision_id)
        return _masked(updated, project_api_revision(updated, api_id, revision_id, stored))

    @router.delete("/apim/management/apis/{api_id}/revisions/{revision_id}")
    async def delete_api_revision(api_id: str, revision_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        _ensure_api_authoring_mode(cfg)
        api = _get_api_or_404(cfg, api_id)
        revision = _get_api_revision_or_404(cfg, api_id, revision_id)
        if revision.is_current or api.revision == revision_id:
            raise HTTPException(status_code=409, detail="Current API revision cannot be deleted")
        for release_id, release in api.releases.items():
            if release.revision == revision_id:
                raise HTTPException(
                    status_code=409,
                    detail=f"API revision is still referenced by release {release_id}",
                )
        del api.revisions[revision_id]
        updated = _persist_or_apply_config(request, cfg)
        return {
            "deleted": True,
            "api_id": api_id,
            "revision_id": revision_id,
            "remaining": len(updated.apis[api_id].revisions),
        }

    @router.get("/apim/management/apis/{api_id}/releases")
    async def list_api_releases(api_id: str, request: Request) -> list[dict[str, Any]]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        api = _get_api_or_404(cfg, api_id)
        return [
            _masked(cfg, project_api_release(cfg, api_id, release_id, release))
            for release_id, release in api.releases.items()
        ]

    @router.get("/apim/management/apis/{api_id}/releases/{release_id}")
    async def get_api_release(api_id: str, release_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        release = _get_api_release_or_404(cfg, api_id, release_id)
        return _masked(cfg, project_api_release(cfg, api_id, release_id, release))

    @router.put("/apim/management/apis/{api_id}/releases/{release_id}")
    async def upsert_api_release(
        api_id: str, release_id: str, request: Request, body: ApiReleaseUpsert
    ) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        _ensure_api_authoring_mode(cfg)
        api = _get_api_or_404(cfg, api_id)
        if body.revision not in api.revisions:
            raise HTTPException(status_code=404, detail="API revision not found")
        existing = api.releases.get(release_id)
        api.releases[release_id] = ApiReleaseConfig(
            name=body.name or (existing.name if existing is not None else release_id),
            api_id=body.api_id or _default_release_api_id(cfg, api_id, body.revision),
            notes=body.notes if body.notes is not None else (existing.notes if existing is not None else None),
            revision=body.revision,
        )
        updated = _persist_or_apply_config(request, cfg)
        stored = _get_api_release_or_404(updated, api_id, release_id)
        return _masked(updated, project_api_release(updated, api_id, release_id, stored))

    @router.delete("/apim/management/apis/{api_id}/releases/{release_id}")
    async def delete_api_release(api_id: str, release_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        _ensure_api_authoring_mode(cfg)
        api = _get_api_or_404(cfg, api_id)
        _get_api_release_or_404(cfg, api_id, release_id)
        del api.releases[release_id]
        updated = _persist_or_apply_config(request, cfg)
        return {
            "deleted": True,
            "api_id": api_id,
            "release_id": release_id,
            "remaining": len(updated.apis[api_id].releases),
        }

    @router.get("/apim/management/apis/{api_id}/tags")
    async def list_api_tags(api_id: str, request: Request) -> list[dict[str, Any]]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        api = _get_api_or_404(cfg, api_id)
        return [
            _masked(cfg, project_api_tag_link(cfg, api_id, tag_id, _get_tag_or_404(cfg, tag_id))) for tag_id in api.tags
        ]

    @router.get("/apim/management/apis/{api_id}/tags/{tag_id}")
    async def get_api_tag(api_id: str, tag_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        api = _get_api_or_404(cfg, api_id)
        if tag_id not in api.tags:
            raise HTTPException(status_code=404, detail="API tag link not found")
        return _masked(cfg, project_api_tag_link(cfg, api_id, tag_id, _get_tag_or_404(cfg, tag_id)))

    @router.put("/apim/management/apis/{api_id}/tags/{tag_id}")
    async def put_api_tag(api_id: str, tag_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        api = _get_api_or_404(cfg, api_id)
        _get_tag_or_404(cfg, tag_id)
        _link_list_item(api.tags, tag_id)
        updated = _persist_or_apply_config(request, cfg)
        return _masked(updated, project_api_tag_link(updated, api_id, tag_id, updated.tags[tag_id]))

    @router.delete("/apim/management/apis/{api_id}/tags/{tag_id}")
    async def delete_api_tag(api_id: str, tag_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        api = _get_api_or_404(cfg, api_id)
        if not _unlink_list_item(api.tags, tag_id):
            raise HTTPException(status_code=404, detail="API tag link not found")
        _persist_or_apply_config(request, cfg)
        return {"deleted": True, "api_id": api_id, "tag_id": tag_id}

    @router.get("/apim/management/apis/{api_id}/schemas/{schema_id}")
    async def get_api_schema(api_id: str, schema_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        schema = _get_api_schema_or_404(cfg, api_id, schema_id)
        return _masked(cfg, project_api_schema(cfg, api_id, schema_id, schema))

    @router.get("/apim/management/apis/{api_id}/operations/{operation_id}")
    async def get_api_operation(api_id: str, operation_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        operation = _get_operation_or_404(cfg, api_id, operation_id)
        return _masked(cfg, project_operation(cfg, api_id, operation_id, operation))

    @router.get("/apim/management/apis/{api_id}/operations/{operation_id}/tags")
    async def list_api_operation_tags(api_id: str, operation_id: str, request: Request) -> list[dict[str, Any]]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        operation = _get_operation_or_404(cfg, api_id, operation_id)
        return [
            _masked(cfg, project_operation_tag_link(cfg, api_id, operation_id, tag_id, _get_tag_or_404(cfg, tag_id)))
            for tag_id in operation.tags
        ]

    @router.get("/apim/management/apis/{api_id}/operations/{operation_id}/tags/{tag_id}")
    async def get_api_operation_tag(api_id: str, operation_id: str, tag_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        operation = _get_operation_or_404(cfg, api_id, operation_id)
        if tag_id not in operation.tags:
            raise HTTPException(status_code=404, detail="Operation tag link not found")
        return _masked(
            cfg,
            project_operation_tag_link(cfg, api_id, operation_id, tag_id, _get_tag_or_404(cfg, tag_id)),
        )

    @router.put("/apim/management/apis/{api_id}/operations/{operation_id}/tags/{tag_id}")
    async def put_api_operation_tag(api_id: str, operation_id: str, tag_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        operation = _get_operation_or_404(cfg, api_id, operation_id)
        _get_tag_or_404(cfg, tag_id)
        _link_list_item(operation.tags, tag_id)
        updated = _persist_or_apply_config(request, cfg)
        return _masked(
            updated,
            project_operation_tag_link(updated, api_id, operation_id, tag_id, updated.tags[tag_id]),
        )

    @router.delete("/apim/management/apis/{api_id}/operations/{operation_id}/tags/{tag_id}")
    async def delete_api_operation_tag(api_id: str, operation_id: str, tag_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        operation = _get_operation_or_404(cfg, api_id, operation_id)
        if not _unlink_list_item(operation.tags, tag_id):
            raise HTTPException(status_code=404, detail="Operation tag link not found")
        _persist_or_apply_config(request, cfg)
        return {"deleted": True, "api_id": api_id, "operation_id": operation_id, "tag_id": tag_id}

    @router.put("/apim/management/apis/{api_id}/operations/{operation_id}")
    async def upsert_api_operation(
        api_id: str, operation_id: str, request: Request, body: OperationUpsert
    ) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        _ensure_api_authoring_mode(cfg)
        api = _get_api_or_404(cfg, api_id)
        _validate_policy_xml(cfg, body.policies_xml)
        existing = api.operations.get(operation_id)
        api.operations[operation_id] = OperationConfig(
            name=body.name or (existing.name if existing is not None else operation_id),
            method=body.method,
            url_template=body.url_template,
            description=body.description
            if body.description is not None
            else (existing.description if existing else None),
            upstream_base_url=body.upstream_base_url,
            upstream_path_prefix=body.upstream_path_prefix,
            backend=body.backend,
            products=body.products,
            api_version_set=body.api_version_set,
            api_version=body.api_version,
            subscription_header_names=body.subscription_header_names,
            subscription_query_param_names=body.subscription_query_param_names,
            authz=body.authz,
            policies_xml=body.policies_xml,
            tags=body.tags if body.tags is not None else (existing.tags if existing is not None else []),
            template_parameters=(
                body.template_parameters
                if body.template_parameters is not None
                else (existing.template_parameters if existing is not None else [])
            ),
            request=body.request if body.request is not None else (existing.request if existing is not None else None),
            responses=body.responses
            if body.responses is not None
            else (existing.responses if existing is not None else []),
        )
        updated = _persist_or_apply_config(request, cfg)
        operation = _get_operation_or_404(updated, api_id, operation_id)
        return _masked(updated, project_operation(updated, api_id, operation_id, operation))

    @router.delete("/apim/management/apis/{api_id}/operations/{operation_id}")
    async def delete_api_operation(api_id: str, operation_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        _ensure_api_authoring_mode(cfg)
        api = _get_api_or_404(cfg, api_id)
        _get_operation_or_404(cfg, api_id, operation_id)
        del api.operations[operation_id]
        updated = _persist_or_apply_config(request, cfg)
        return {
            "deleted": True,
            "api_id": api_id,
            "operation_id": operation_id,
            "remaining": len(updated.apis[api_id].operations),
        }

    @router.get("/apim/management/policies/{scope_type}/{scope_name:path}")
    async def management_get_policy(scope_type: str, scope_name: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        target = _policy_scope_target(cfg, scope_type, scope_name)
        return {
            "scope_type": scope_type,
            "scope_name": scope_name,
            "xml": _policy_xml_for_target(target),
        }

    @router.put("/apim/management/policies/{scope_type}/{scope_name:path}")
    async def management_put_policy(
        scope_type: str,
        scope_name: str,
        request: Request,
        body: PolicyUpdate,
    ) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        xml = body.xml.strip() or EMPTY_POLICY_XML
        parse_policies_xml(xml, policy_fragments=cfg.policy_fragments)
        target = _policy_scope_target(cfg, scope_type, scope_name)
        _set_policy_xml(target, xml)
        updated = _persist_or_apply_config(request, cfg)
        return {
            "scope_type": scope_type,
            "scope_name": scope_name,
            "xml": _policy_xml_for_target(_policy_scope_target(updated, scope_type, scope_name)),
        }

    @router.get("/apim/management/traces")
    async def management_traces(request: Request, limit: int = 50) -> dict[str, Any]:
        _require_tenant_access(request)
        trace_store: dict[str, Any] = request.app.state.trace_store
        items = sorted(trace_store.values(), key=lambda item: item.get("created_at", ""), reverse=True)
        return {"items": items[: max(1, min(limit, 200))]}

    @router.post("/apim/management/replay")
    async def management_replay(request: Request, body: ReplayRequestBody) -> dict[str, Any]:
        _require_tenant_access(request)
        path = body.path if body.path.startswith("/") else f"/{body.path}"
        if path.startswith("/apim/management") or path.startswith("/apim/admin"):
            raise HTTPException(status_code=400, detail="Replay path must target gateway routes")

        headers = dict(body.headers)
        headers.setdefault("x-apim-trace", "true")
        content = b""
        if body.body_base64 is not None:
            try:
                content = base64.b64decode(body.body_base64)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid base64 replay body") from exc
        elif body.body_text is not None:
            content = body.body_text.encode("utf-8")

        transport = httpx.ASGITransport(app=request.app)
        async with httpx.AsyncClient(transport=transport, base_url="https://apim-replay.local") as replay_client:
            response = await replay_client.request(
                body.method.upper(),
                path,
                params=body.query,
                headers=headers,
                content=content,
            )

        trace_id = response.headers.get("x-apim-trace-id")
        decoded = _decode_body(response.content)
        return {
            "request": body.model_dump(),
            "response": {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body_text": decoded["text"],
                "body_base64": decoded["base64"],
            },
            "trace_id": trace_id,
            "trace": request.app.state.trace_store.get(trace_id) if trace_id else None,
        }

    @router.get("/apim/management/products")
    async def list_products(request: Request) -> list[dict[str, Any]]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        return [_masked(cfg, project_product(cfg, product_id, product)) for product_id, product in cfg.products.items()]

    @router.get("/apim/management/products/{product_id}")
    async def get_product(product_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        product = _get_product_or_404(cfg, product_id)
        return _masked(cfg, project_product(cfg, product_id, product))

    @router.put("/apim/management/products/{product_id}")
    async def upsert_product(product_id: str, request: Request, body: ProductUpsert) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        updated = require_management_plane().upsert_product(cfg, product_id, body)
        product = _get_product_or_404(updated, product_id)
        return _masked(updated, project_product(updated, product_id, product))

    @router.delete("/apim/management/products/{product_id}")
    async def delete_product(product_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        updated = require_management_plane().delete_product(cfg, product_id)
        return {"deleted": True, "product_id": product_id, "remaining": len(updated.products)}

    @router.get("/apim/management/products/{product_id}/groups")
    async def list_product_groups(product_id: str, request: Request) -> list[dict[str, Any]]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        product = _get_product_or_404(cfg, product_id)
        return [
            _masked(cfg, project_product_group_link(cfg, product_id, group_id, _get_group_or_404(cfg, group_id)))
            for group_id in product.groups
        ]

    @router.get("/apim/management/products/{product_id}/groups/{group_id}")
    async def get_product_group(product_id: str, group_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        product = _get_product_or_404(cfg, product_id)
        if group_id not in product.groups:
            raise HTTPException(status_code=404, detail="Product group link not found")
        return _masked(cfg, project_product_group_link(cfg, product_id, group_id, _get_group_or_404(cfg, group_id)))

    @router.put("/apim/management/products/{product_id}/groups/{group_id}")
    async def put_product_group(product_id: str, group_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        product = _get_product_or_404(cfg, product_id)
        _get_group_or_404(cfg, group_id)
        _link_list_item(product.groups, group_id)
        updated = _persist_or_apply_config(request, cfg)
        return _masked(updated, project_product_group_link(updated, product_id, group_id, updated.groups[group_id]))

    @router.delete("/apim/management/products/{product_id}/groups/{group_id}")
    async def delete_product_group(product_id: str, group_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        product = _get_product_or_404(cfg, product_id)
        if not _unlink_list_item(product.groups, group_id):
            raise HTTPException(status_code=404, detail="Product group link not found")
        _persist_or_apply_config(request, cfg)
        return {"deleted": True, "product_id": product_id, "group_id": group_id}

    @router.get("/apim/management/products/{product_id}/tags")
    async def list_product_tags(product_id: str, request: Request) -> list[dict[str, Any]]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        product = _get_product_or_404(cfg, product_id)
        return [
            _masked(cfg, project_product_tag_link(cfg, product_id, tag_id, _get_tag_or_404(cfg, tag_id)))
            for tag_id in product.tags
        ]

    @router.get("/apim/management/products/{product_id}/tags/{tag_id}")
    async def get_product_tag(product_id: str, tag_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        product = _get_product_or_404(cfg, product_id)
        if tag_id not in product.tags:
            raise HTTPException(status_code=404, detail="Product tag link not found")
        return _masked(cfg, project_product_tag_link(cfg, product_id, tag_id, _get_tag_or_404(cfg, tag_id)))

    @router.put("/apim/management/products/{product_id}/tags/{tag_id}")
    async def put_product_tag(product_id: str, tag_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        product = _get_product_or_404(cfg, product_id)
        _get_tag_or_404(cfg, tag_id)
        _link_list_item(product.tags, tag_id)
        updated = _persist_or_apply_config(request, cfg)
        return _masked(updated, project_product_tag_link(updated, product_id, tag_id, updated.tags[tag_id]))

    @router.delete("/apim/management/products/{product_id}/tags/{tag_id}")
    async def delete_product_tag(product_id: str, tag_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        product = _get_product_or_404(cfg, product_id)
        if not _unlink_list_item(product.tags, tag_id):
            raise HTTPException(status_code=404, detail="Product tag link not found")
        _persist_or_apply_config(request, cfg)
        return {"deleted": True, "product_id": product_id, "tag_id": tag_id}

    @router.get("/apim/management/tags")
    async def list_tags(request: Request) -> list[dict[str, Any]]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        return [_masked(cfg, project_tag(cfg, tag_id, tag)) for tag_id, tag in cfg.tags.items()]

    @router.get("/apim/management/tags/{tag_id}")
    async def get_tag(tag_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        tag = _get_tag_or_404(cfg, tag_id)
        return _masked(cfg, project_tag(cfg, tag_id, tag))

    @router.put("/apim/management/tags/{tag_id}")
    async def upsert_tag(tag_id: str, request: Request, body: TagUpsert) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        updated = require_management_plane().upsert_tag(cfg, tag_id, body)
        tag = _get_tag_or_404(updated, tag_id)
        return _masked(updated, project_tag(updated, tag_id, tag))

    @router.delete("/apim/management/tags/{tag_id}")
    async def delete_tag(tag_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        updated = require_management_plane().delete_tag(cfg, tag_id)
        return {"deleted": True, "tag_id": tag_id, "remaining": len(updated.tags)}

    @router.get("/apim/management/subscriptions")
    async def list_subscriptions(request: Request) -> list[dict[str, Any]]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        return [
            _masked(cfg, project_subscription(cfg, config_key, subscription))
            for config_key, subscription in cfg.subscription.subscriptions.items()
        ]

    @router.get("/apim/management/subscriptions/{subscription_id}")
    async def get_subscription(subscription_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        entry = _find_subscription_entry(cfg, subscription_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="Subscription not found")
        config_key, subscription = entry
        return _masked(cfg, project_subscription(cfg, config_key, subscription))

    @router.post("/apim/management/subscriptions")
    async def create_subscription(request: Request, body: SubscriptionUpsert) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        manager = require_management_plane()
        updated = manager.create_subscription(cfg, body)
        entry = manager.find_subscription_entry(updated, body.id)
        if entry is None:
            raise HTTPException(status_code=500, detail="Subscription persistence failed")
        config_key, subscription = entry
        return _masked(updated, project_subscription(updated, config_key, subscription))

    @router.patch("/apim/management/subscriptions/{subscription_id}")
    async def update_subscription(request: Request, subscription_id: str, body: SubscriptionUpdate) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        manager = require_management_plane()
        updated = manager.update_subscription(cfg, subscription_id, body)
        entry = manager.find_subscription_entry(updated, subscription_id)
        if entry is None:
            raise HTTPException(status_code=500, detail="Subscription persistence failed")
        config_key, subscription = entry
        return _masked(updated, project_subscription(updated, config_key, subscription))

    @router.delete("/apim/management/subscriptions/{subscription_id}")
    async def delete_subscription(subscription_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        updated = require_management_plane().delete_subscription(cfg, subscription_id)
        return {
            "deleted": True,
            "subscription_id": subscription_id,
            "remaining": len(updated.subscription.subscriptions),
        }

    @router.post("/apim/management/subscriptions/{subscription_id}/rotate")
    async def management_rotate_subscription_key(
        subscription_id: str, request: Request, key: str = "secondary"
    ) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        manager = require_management_plane()
        updated, new_key = manager.rotate_subscription_key(cfg, subscription_id, key)
        entry = manager.find_subscription_entry(updated, subscription_id)
        if entry is None:
            raise HTTPException(status_code=500, detail="Subscription persistence failed")
        config_key, subscription = entry
        return {
            "subscription_id": subscription.id,
            "subscription_name": subscription.name,
            "rotated": key,
            "new_key": new_key,
            "subscription": _masked(updated, project_subscription(updated, config_key, subscription)),
        }

    def _require_portal_enabled(cfg: GatewayConfig) -> None:
        if not cfg.portal.enabled:
            raise HTTPException(status_code=404, detail="Portal is not enabled")

    def _portal_user_id(request: Request, cfg: GatewayConfig) -> str:
        user = require_portal_user(cfg, request.headers.get(cfg.portal.user_header))
        return user.id

    @router.get("/apim/portal", response_class=HTMLResponse)
    async def portal_page(request: Request) -> HTMLResponse:
        cfg: GatewayConfig = request.app.state.gateway_config
        _require_portal_enabled(cfg)
        return HTMLResponse(PORTAL_HTML)

    @router.get("/apim/portal/users")
    async def portal_list_users(request: Request) -> dict[str, Any]:
        cfg: GatewayConfig = request.app.state.gateway_config
        _require_portal_enabled(cfg)
        return portal_users(cfg)

    @router.get("/apim/portal/catalog")
    async def portal_get_catalog(request: Request) -> dict[str, Any]:
        cfg: GatewayConfig = request.app.state.gateway_config
        _require_portal_enabled(cfg)
        return portal_catalog(cfg, _portal_user_id(request, cfg))

    @router.get("/apim/portal/subscriptions")
    async def portal_list_subscriptions(request: Request) -> dict[str, Any]:
        cfg: GatewayConfig = request.app.state.gateway_config
        _require_portal_enabled(cfg)
        return portal_subscriptions(cfg, _portal_user_id(request, cfg))

    @router.post("/apim/portal/subscriptions", status_code=201)
    async def portal_request_subscription(request: Request, body: PortalSubscriptionRequest) -> dict[str, Any]:
        cfg: GatewayConfig = request.app.state.gateway_config
        _require_portal_enabled(cfg)
        user_id = _portal_user_id(request, cfg)
        subscription = create_portal_subscription(cfg, user_id, body.product_id, body.name)
        updated = require_management_plane().persist_or_apply_config(cfg)
        persisted = updated.subscription.subscriptions.get(subscription.id)
        if persisted is None:
            raise HTTPException(status_code=500, detail="Subscription persistence failed")
        return project_portal_subscription(persisted)

    @router.get("/apim/management/backends")
    async def list_backends(request: Request) -> list[dict[str, Any]]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        return [_masked(cfg, project_backend(cfg, backend_id, backend)) for backend_id, backend in cfg.backends.items()]

    @router.get("/apim/management/backends/{backend_id}")
    async def get_backend(backend_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        backend = _get_backend_or_404(cfg, backend_id)
        return _masked(cfg, project_backend(cfg, backend_id, backend))

    @router.put("/apim/management/backends/{backend_id}")
    async def upsert_backend(backend_id: str, request: Request, body: BackendUpsert) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        cfg.backends[backend_id] = BackendConfig(**body.model_dump(mode="json"))
        updated = _persist_or_apply_config(request, cfg)
        backend = _get_backend_or_404(updated, backend_id)
        return _masked(updated, project_backend(updated, backend_id, backend))

    @router.delete("/apim/management/backends/{backend_id}")
    async def delete_backend(backend_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        _get_backend_or_404(cfg, backend_id)
        del cfg.backends[backend_id]
        updated = _persist_or_apply_config(request, cfg)
        return {"deleted": True, "backend_id": backend_id, "remaining": len(updated.backends)}

    @router.get("/apim/management/named-values")
    async def list_named_values(request: Request) -> list[dict[str, Any]]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        return [
            _masked(cfg, project_named_value(cfg, named_value_id, named_value))
            for named_value_id, named_value in cfg.named_values.items()
        ]

    @router.get("/apim/management/named-values/{named_value_id}")
    async def get_named_value(named_value_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        named_value = _get_named_value_or_404(cfg, named_value_id)
        return _masked(cfg, project_named_value(cfg, named_value_id, named_value))

    @router.get("/apim/management/loggers")
    async def list_loggers(request: Request) -> list[dict[str, Any]]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        return [_masked(cfg, project_logger(cfg, logger_id, logger)) for logger_id, logger in cfg.loggers.items()]

    @router.get("/apim/management/loggers/{logger_id}")
    async def get_logger(logger_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        logger_entry = _get_logger_or_404(cfg, logger_id)
        return _masked(cfg, project_logger(cfg, logger_id, logger_entry))

    @router.get("/apim/management/diagnostics")
    async def list_diagnostics(request: Request) -> list[dict[str, Any]]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        return [
            _masked(cfg, project_diagnostic(cfg, diagnostic_id, diagnostic))
            for diagnostic_id, diagnostic in cfg.diagnostics.items()
        ]

    @router.get("/apim/management/diagnostics/{diagnostic_id}")
    async def get_diagnostic(diagnostic_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        diagnostic = _get_diagnostic_or_404(cfg, diagnostic_id)
        return _masked(cfg, project_diagnostic(cfg, diagnostic_id, diagnostic))

    @router.put("/apim/management/named-values/{named_value_id}")
    async def upsert_named_value(named_value_id: str, request: Request, body: NamedValueUpsert) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        cfg.named_values[named_value_id] = NamedValueConfig(**body.model_dump(mode="json"))
        updated = _persist_or_apply_config(request, cfg)
        named_value = _get_named_value_or_404(updated, named_value_id)
        return _masked(updated, project_named_value(updated, named_value_id, named_value))

    @router.delete("/apim/management/named-values/{named_value_id}")
    async def delete_named_value(named_value_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        _get_named_value_or_404(cfg, named_value_id)
        del cfg.named_values[named_value_id]
        updated = _persist_or_apply_config(request, cfg)
        return {"deleted": True, "named_value_id": named_value_id, "remaining": len(updated.named_values)}

    @router.get("/apim/management/api-version-sets")
    async def list_api_version_sets(request: Request) -> list[dict[str, Any]]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        return [
            _masked(cfg, project_api_version_set(cfg, version_set_id, version_set))
            for version_set_id, version_set in cfg.api_version_sets.items()
        ]

    @router.get("/apim/management/api-version-sets/{version_set_id}")
    async def get_api_version_set(version_set_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        version_set = cfg.api_version_sets.get(version_set_id)
        if version_set is None:
            raise HTTPException(status_code=404, detail="API version set not found")
        return _masked(cfg, project_api_version_set(cfg, version_set_id, version_set))

    @router.put("/apim/management/api-version-sets/{version_set_id}")
    async def upsert_api_version_set(
        version_set_id: str, request: Request, body: ApiVersionSetUpsert
    ) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        _ensure_api_authoring_mode(cfg)
        cfg.api_version_sets[version_set_id] = ApiVersionSetConfig(
            display_name=body.display_name,
            description=body.description,
            versioning_scheme=_coerce_api_versioning_scheme(body.versioning_scheme),
            version_header_name=body.version_header_name,
            version_query_name=body.version_query_name,
            default_version=body.default_version,
        )
        updated = _persist_or_apply_config(request, cfg)
        return _masked(
            updated,
            project_api_version_set(updated, version_set_id, updated.api_version_sets[version_set_id]),
        )

    @router.delete("/apim/management/api-version-sets/{version_set_id}")
    async def delete_api_version_set(version_set_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        _ensure_api_authoring_mode(cfg)
        version_set = cfg.api_version_sets.get(version_set_id)
        if version_set is None:
            raise HTTPException(status_code=404, detail="API version set not found")

        for api_id, api in cfg.apis.items():
            if api.api_version_set == version_set_id:
                raise HTTPException(
                    status_code=409,
                    detail=f"API version set is still in use by API {api_id}",
                )
            for operation_id, operation in api.operations.items():
                if operation.api_version_set == version_set_id:
                    raise HTTPException(
                        status_code=409,
                        detail=f"API version set is still in use by operation {api_id}:{operation_id}",
                    )

        del cfg.api_version_sets[version_set_id]
        updated = _persist_or_apply_config(request, cfg)
        return {"deleted": True, "version_set_id": version_set_id, "remaining": len(updated.api_version_sets)}

    @router.get("/apim/management/policy-fragments")
    async def list_policy_fragments(request: Request) -> list[dict[str, Any]]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        return [
            _masked(cfg, project_policy_fragment(cfg, fragment_id, xml))
            for fragment_id, xml in cfg.policy_fragments.items()
        ]

    @router.get("/apim/management/policy-fragments/{fragment_id}")
    async def get_policy_fragment(fragment_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        xml = cfg.policy_fragments.get(fragment_id)
        if xml is None:
            raise HTTPException(status_code=404, detail="Policy fragment not found")
        return _masked(cfg, project_policy_fragment(cfg, fragment_id, xml))

    @router.put("/apim/management/policy-fragments/{fragment_id}")
    async def upsert_policy_fragment(fragment_id: str, request: Request, body: PolicyFragmentUpsert) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        _validate_fragment_xml(body.xml)
        cfg.policy_fragments[fragment_id] = body.xml
        updated = _persist_or_apply_config(request, cfg)
        return _masked(updated, project_policy_fragment(updated, fragment_id, updated.policy_fragments[fragment_id]))

    @router.delete("/apim/management/policy-fragments/{fragment_id}")
    async def delete_policy_fragment(fragment_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        if fragment_id not in cfg.policy_fragments:
            raise HTTPException(status_code=404, detail="Policy fragment not found")
        del cfg.policy_fragments[fragment_id]
        updated = _persist_or_apply_config(request, cfg)
        return {"deleted": True, "fragment_id": fragment_id, "remaining": len(updated.policy_fragments)}

    @router.get("/apim/management/users")
    async def list_users(request: Request) -> list[dict[str, Any]]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        return [_masked(cfg, project_user(cfg, user_id, user)) for user_id, user in cfg.users.items()]

    @router.get("/apim/management/users/{user_id}")
    async def get_user(user_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        user = _get_user_or_404(cfg, user_id)
        return _masked(cfg, project_user(cfg, user_id, user))

    @router.put("/apim/management/users/{user_id}")
    async def upsert_user(user_id: str, request: Request, body: UserUpsert) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        updated = require_management_plane().upsert_user(cfg, user_id, body)
        user = _get_user_or_404(updated, user_id)
        return _masked(updated, project_user(updated, user_id, user))

    @router.delete("/apim/management/users/{user_id}")
    async def delete_user(user_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        updated = require_management_plane().delete_user(cfg, user_id)
        return {"deleted": True, "user_id": user_id, "remaining": len(updated.users)}

    @router.get("/apim/management/groups")
    async def list_groups(request: Request) -> list[dict[str, Any]]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        return [_masked(cfg, project_group(cfg, group_id, group)) for group_id, group in cfg.groups.items()]

    @router.get("/apim/management/groups/{group_id}/users")
    async def list_group_users(group_id: str, request: Request) -> list[dict[str, Any]]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        group = _get_group_or_404(cfg, group_id)
        return [
            _masked(cfg, project_group_user_link(cfg, group_id, user_id, _get_user_or_404(cfg, user_id)))
            for user_id in group.users
        ]

    @router.get("/apim/management/groups/{group_id}/users/{user_id}")
    async def get_group_user(group_id: str, user_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        group = _get_group_or_404(cfg, group_id)
        if user_id not in group.users:
            raise HTTPException(status_code=404, detail="Group user link not found")
        return _masked(cfg, project_group_user_link(cfg, group_id, user_id, _get_user_or_404(cfg, user_id)))

    @router.put("/apim/management/groups/{group_id}/users/{user_id}")
    async def put_group_user(group_id: str, user_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        group = _get_group_or_404(cfg, group_id)
        _get_user_or_404(cfg, user_id)
        _link_list_item(group.users, user_id)
        updated = _persist_or_apply_config(request, cfg)
        return _masked(updated, project_group_user_link(updated, group_id, user_id, updated.users[user_id]))

    @router.delete("/apim/management/groups/{group_id}/users/{user_id}")
    async def delete_group_user(group_id: str, user_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        group = _get_group_or_404(cfg, group_id)
        if not _unlink_list_item(group.users, user_id):
            raise HTTPException(status_code=404, detail="Group user link not found")
        _persist_or_apply_config(request, cfg)
        return {"deleted": True, "group_id": group_id, "user_id": user_id}

    @router.get("/apim/management/groups/{group_id}")
    async def get_group(group_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        group = _get_group_or_404(cfg, group_id)
        return _masked(cfg, project_group(cfg, group_id, group))

    @router.put("/apim/management/groups/{group_id}")
    async def upsert_group(group_id: str, request: Request, body: GroupUpsert) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        updated = require_management_plane().upsert_group(cfg, group_id, body)
        group = _get_group_or_404(updated, group_id)
        return _masked(updated, project_group(updated, group_id, group))

    @router.delete("/apim/management/groups/{group_id}")
    async def delete_group(group_id: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        updated = require_management_plane().delete_group(cfg, group_id)
        return {"deleted": True, "group_id": group_id, "remaining": len(updated.groups)}

    @router.post("/apim/management/import/tofu-show")
    async def import_tofu_show_json(request: Request, tf: dict[str, Any]) -> dict:
        _require_tenant_access(request)

        current: GatewayConfig = request.app.state.gateway_config
        result = import_from_tofu_show_json(tf)
        imported = result.config

        # Preserve local runtime settings.
        imported.allowed_origins = current.allowed_origins
        imported.allow_anonymous = current.allow_anonymous
        imported.oidc = current.oidc
        imported.oidc_providers = current.oidc_providers
        imported.admin_token = current.admin_token
        imported.tenant_access = current.tenant_access
        imported.trace_enabled = current.trace_enabled
        imported.policy_fragments = current.policy_fragments
        imported_client_certificate_mode = imported.client_certificate.mode
        imported.client_certificate = current.client_certificate.model_copy(deep=True)
        if imported_client_certificate_mode.value != "disabled":
            imported.client_certificate.mode = imported_client_certificate_mode
        if not result.service_imported:
            imported.service = current.service

        require_management_plane().apply_runtime_config(imported)
        request.app.state.cache = {}
        request.app.state.policy_cache = {}
        request.app.state.policy_response_cache = {}
        request.app.state.policy_value_cache = {}
        request.app.state.rate_limit_store = {}
        request.app.state.quota_store = {}
        request.app.state.trace_store = {}

        return {
            "routes": len(imported.routes),
            "products": len(imported.products),
            "loggers": len(imported.loggers),
            "apim_diagnostics": len(imported.diagnostics),
            "groups": len(imported.groups),
            "tags": len(imported.tags),
            "subscriptions": len(imported.subscription.subscriptions),
            "apis": len(imported.apis),
            "api_revisions": sum(len(api.revisions) for api in imported.apis.values()),
            "api_releases": sum(len(api.releases) for api in imported.apis.values()),
            "diagnostics": [item.__dict__ for item in result.diagnostics],
        }

    return router
