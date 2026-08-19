from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.config import (
    ApiConfig,
    ApiReleaseConfig,
    ApiRevisionConfig,
    ApiVersioningScheme,
    ApiVersionSetConfig,
    BackendConfig,
    GatewayConfig,
    GroupConfig,
    NamedValueConfig,
    OperationConfig,
    ProductConfig,
    Subscription,
    SubscriptionKeyPair,
    TagConfig,
    UserConfig,
    load_config,
)
from app.effective_policy import (
    EMPTY_POLICY_XML,
    effective_policy_xml,
    policy_xml_documents_for_target,
)
from app.openapi_import import parse_api_import
from app.policy import parse_policies_xml
from app.security import OIDCVerifier
from app.terraform_import import import_from_tofu_show_json

logger = logging.getLogger("apim-simulator")


class ManagementService:
    def __init__(
        self,
        *,
        app: Any,
        serialize_gateway_config: Callable[[GatewayConfig], str],
        build_oidc_verifiers: Callable[[GatewayConfig], dict[str, OIDCVerifier]],
    ) -> None:
        self.app = app
        self._serialize_gateway_config = serialize_gateway_config
        self._build_oidc_verifiers = build_oidc_verifiers

    def reload_config(self) -> GatewayConfig:
        new_config = load_config()
        new_config.routes = new_config.materialize_routes()
        self.app.state.gateway_config = new_config
        self.app.state.oidc_verifiers = self._build_oidc_verifiers(new_config)
        self.app.state.policy_cache = {}
        self.app.state.policy_response_cache = {}
        self.app.state.policy_value_cache = {}
        metrics = getattr(self.app.state, "gateway_metrics", None)
        if metrics is not None:
            metrics.config_reloads.add(1, {"result": "success"})
        logger.info(
            "config reloaded | routes=%d | origins=%s | anonymous=%s",
            len(new_config.routes),
            new_config.allowed_origins,
            new_config.allow_anonymous,
        )
        return new_config

    def apply_runtime_config(self, cfg: GatewayConfig) -> GatewayConfig:
        cfg.routes = cfg.materialize_routes()
        self.app.state.gateway_config = cfg
        self.app.state.oidc_verifiers = self._build_oidc_verifiers(cfg)
        self.app.state.policy_cache = {}
        self.app.state.policy_response_cache = {}
        self.app.state.policy_value_cache = {}
        return cfg

    def persist_or_apply_config(self, cfg: GatewayConfig) -> GatewayConfig:
        config_path = os.getenv("APIM_CONFIG_PATH", "").strip()
        if not config_path:
            return self.apply_runtime_config(cfg)

        try:
            Path(config_path).write_text(self._serialize_gateway_config(cfg), encoding="utf-8")
        except OSError as exc:
            raise HTTPException(status_code=500, detail="Unable to persist config update") from exc
        return self.reload_config()

    def upsert_product(self, cfg: GatewayConfig, product_id: str, body: Any) -> GatewayConfig:
        existing = cfg.products.get(product_id)
        cfg.products[product_id] = ProductConfig(
            name=body.name,
            description=body.description,
            state=body.state,
            require_subscription=body.require_subscription,
            approval_required=body.approval_required,
            groups=existing.groups if existing is not None else [],
            tags=existing.tags if existing is not None else [],
        )
        return self.persist_or_apply_config(cfg)

    def delete_product(self, cfg: GatewayConfig, product_id: str) -> GatewayConfig:
        self._get_product_or_404(cfg, product_id)
        del cfg.products[product_id]
        for subscription in cfg.subscription.subscriptions.values():
            subscription.products = [item for item in subscription.products if item != product_id]
        for api in cfg.apis.values():
            api.products = [item for item in api.products if item != product_id]
            for operation in api.operations.values():
                if operation.products is not None:
                    operation.products = [item for item in operation.products if item != product_id]
        for route in cfg.routes:
            if route.product == product_id:
                route.product = None
            route.products = [item for item in route.products if item != product_id]
        return self.persist_or_apply_config(cfg)

    def upsert_tag(self, cfg: GatewayConfig, tag_id: str, body: Any) -> GatewayConfig:
        cfg.tags[tag_id] = TagConfig(display_name=body.display_name or tag_id)
        return self.persist_or_apply_config(cfg)

    def delete_tag(self, cfg: GatewayConfig, tag_id: str) -> GatewayConfig:
        self._get_tag_or_404(cfg, tag_id)
        del cfg.tags[tag_id]
        for api in cfg.apis.values():
            self._unlink_list_item(api.tags, tag_id)
            for operation in api.operations.values():
                self._unlink_list_item(operation.tags, tag_id)
        for product in cfg.products.values():
            self._unlink_list_item(product.tags, tag_id)
        return self.persist_or_apply_config(cfg)

    def upsert_group(self, cfg: GatewayConfig, group_id: str, body: Any) -> GatewayConfig:
        existing = cfg.groups.get(group_id)
        cfg.groups[group_id] = GroupConfig(
            id=group_id,
            name=body.name,
            description=body.description,
            external_id=body.external_id,
            type=body.type,
            users=existing.users if existing is not None else [],
        )
        return self.persist_or_apply_config(cfg)

    def delete_group(self, cfg: GatewayConfig, group_id: str) -> GatewayConfig:
        self._get_group_or_404(cfg, group_id)
        del cfg.groups[group_id]
        for product in cfg.products.values():
            self._unlink_list_item(product.groups, group_id)
        return self.persist_or_apply_config(cfg)

    def upsert_user(self, cfg: GatewayConfig, user_id: str, body: Any) -> GatewayConfig:
        first_name = body.first_name.strip() if body.first_name else None
        last_name = body.last_name.strip() if body.last_name else None
        full_name = " ".join(part for part in [first_name, last_name] if part).strip() or user_id
        cfg.users[user_id] = UserConfig(
            id=user_id,
            email=body.email,
            name=full_name,
            first_name=first_name,
            last_name=last_name,
            note=body.note,
            state=body.state,
            confirmation=body.confirmation,
        )
        return self.persist_or_apply_config(cfg)

    def delete_user(self, cfg: GatewayConfig, user_id: str) -> GatewayConfig:
        self._get_user_or_404(cfg, user_id)
        del cfg.users[user_id]
        for group in cfg.groups.values():
            self._unlink_list_item(group.users, user_id)
        return self.persist_or_apply_config(cfg)

    def create_subscription(self, cfg: GatewayConfig, body: Any) -> GatewayConfig:
        if self.find_subscription_by_id(cfg, body.id) is not None:
            raise HTTPException(status_code=409, detail="Subscription already exists")

        primary = body.primary_key or f"sub-{body.id}-primary"
        secondary = body.secondary_key or f"sub-{body.id}-secondary"
        cfg.subscription.subscriptions[body.id] = Subscription(
            id=body.id,
            name=body.name,
            keys=SubscriptionKeyPair(primary=primary, secondary=secondary),
            state=body.state,
            products=body.products,
            created_by="management",
        )
        return self.persist_or_apply_config(cfg)

    def update_subscription(self, cfg: GatewayConfig, subscription_id: str, body: Any) -> GatewayConfig:
        sub = self.find_subscription_by_id(cfg, subscription_id)
        if sub is None:
            raise HTTPException(status_code=404, detail="Subscription not found")

        if body.name is not None:
            sub.name = body.name
        if body.state is not None:
            sub.state = body.state
        if body.products is not None:
            sub.products = body.products
        return self.persist_or_apply_config(cfg)

    def delete_subscription(self, cfg: GatewayConfig, subscription_id: str) -> GatewayConfig:
        entry = self.find_subscription_entry(cfg, subscription_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="Subscription not found")
        config_key, _subscription = entry
        del cfg.subscription.subscriptions[config_key]
        return self.persist_or_apply_config(cfg)

    def rotate_subscription_key(
        self, cfg: GatewayConfig, subscription_id: str, key: str = "secondary"
    ) -> tuple[GatewayConfig, str]:
        sub = self.find_subscription_by_id(cfg, subscription_id)
        if sub is None:
            raise HTTPException(status_code=404, detail="Subscription not found")
        if key not in {"primary", "secondary"}:
            raise HTTPException(status_code=400, detail="Invalid key")

        new_key = f"rotated-{sub.id}-{key}"
        if key == "primary":
            sub.keys.primary = new_key
        else:
            sub.keys.secondary = new_key
        return self.persist_or_apply_config(cfg), new_key

    def find_subscription_entry(self, cfg: GatewayConfig, subscription_id: str) -> tuple[str, Subscription] | None:
        return cfg.subscription.find_entry(subscription_id)

    def find_subscription_by_id(self, cfg: GatewayConfig, subscription_id: str) -> Subscription | None:
        return cfg.subscription.find_by_id(subscription_id)

    def require_api_authoring_mode(self, cfg: GatewayConfig) -> None:
        if not cfg.apis and cfg.routes:
            raise HTTPException(
                status_code=400,
                detail="API CRUD requires api-authored config; convert legacy route configs before mutating APIs.",
            )

    def validate_policy_xml(self, cfg: GatewayConfig, xml: str | None) -> None:
        if xml is None:
            return
        parse_policies_xml(xml.strip() or EMPTY_POLICY_XML, policy_fragments=cfg.policy_fragments)

    def validate_fragment_xml(self, xml: str) -> None:
        from defusedxml import ElementTree

        try:
            ElementTree.fromstring(f"<fragment>{xml}</fragment>")
        except ElementTree.ParseError as exc:
            raise HTTPException(status_code=400, detail="Invalid policy fragment XML") from exc

    def coerce_api_versioning_scheme(self, raw: str) -> ApiVersioningScheme:
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

    def policy_scope_target(self, cfg: GatewayConfig, scope_type: str, scope_name: str) -> Any:
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

    def policy_xml_for_target(self, target: Any) -> str:
        return effective_policy_xml(policy_xml_documents_for_target(target))

    def set_policy_xml(self, target: Any, xml: str) -> None:
        target.policies_xml = xml
        if hasattr(target, "policies_xml_documents"):
            target.policies_xml_documents = []

    def put_policy(self, cfg: GatewayConfig, scope_type: str, scope_name: str, xml: str) -> GatewayConfig:
        cleaned = xml.strip() or EMPTY_POLICY_XML
        parse_policies_xml(cleaned, policy_fragments=cfg.policy_fragments)
        target = self.policy_scope_target(cfg, scope_type, scope_name)
        self.set_policy_xml(target, cleaned)
        return self.persist_or_apply_config(cfg)

    def import_api(self, cfg: GatewayConfig, api_id: str, body: Any) -> tuple[GatewayConfig, Any]:
        self.require_api_authoring_mode(cfg)
        self.validate_policy_xml(cfg, body.policies_xml)
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
        return self.persist_or_apply_config(cfg), imported

    def upsert_api(self, cfg: GatewayConfig, api_id: str, body: Any) -> GatewayConfig:
        self.require_api_authoring_mode(cfg)
        self.validate_policy_xml(cfg, body.policies_xml)
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
        return self.persist_or_apply_config(cfg)

    def delete_api(self, cfg: GatewayConfig, api_id: str) -> GatewayConfig:
        self.require_api_authoring_mode(cfg)
        self._get_api_or_404(cfg, api_id)
        del cfg.apis[api_id]
        return self.persist_or_apply_config(cfg)

    def upsert_api_revision(self, cfg: GatewayConfig, api_id: str, revision_id: str, body: Any) -> GatewayConfig:
        self.require_api_authoring_mode(cfg)
        api = self._get_api_or_404(cfg, api_id)
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
            self._set_current_revision(api, revision_id, revision)
        return self.persist_or_apply_config(cfg)

    def delete_api_revision(self, cfg: GatewayConfig, api_id: str, revision_id: str) -> GatewayConfig:
        self.require_api_authoring_mode(cfg)
        api = self._get_api_or_404(cfg, api_id)
        revision = self._get_api_revision_or_404(cfg, api_id, revision_id)
        if revision.is_current or api.revision == revision_id:
            raise HTTPException(status_code=409, detail="Current API revision cannot be deleted")
        for release_id, release in api.releases.items():
            if release.revision == revision_id:
                raise HTTPException(
                    status_code=409,
                    detail=f"API revision is still referenced by release {release_id}",
                )
        del api.revisions[revision_id]
        return self.persist_or_apply_config(cfg)

    def upsert_api_release(self, cfg: GatewayConfig, api_id: str, release_id: str, body: Any) -> GatewayConfig:
        self.require_api_authoring_mode(cfg)
        api = self._get_api_or_404(cfg, api_id)
        if body.revision not in api.revisions:
            raise HTTPException(status_code=404, detail="API revision not found")
        existing = api.releases.get(release_id)
        api.releases[release_id] = ApiReleaseConfig(
            name=body.name or (existing.name if existing is not None else release_id),
            api_id=body.api_id or f"service/{cfg.service.name}/apis/{api_id};rev={body.revision}",
            notes=body.notes if body.notes is not None else (existing.notes if existing is not None else None),
            revision=body.revision,
        )
        return self.persist_or_apply_config(cfg)

    def delete_api_release(self, cfg: GatewayConfig, api_id: str, release_id: str) -> GatewayConfig:
        self.require_api_authoring_mode(cfg)
        api = self._get_api_or_404(cfg, api_id)
        self._get_api_release_or_404(cfg, api_id, release_id)
        del api.releases[release_id]
        return self.persist_or_apply_config(cfg)

    def link_api_tag(self, cfg: GatewayConfig, api_id: str, tag_id: str) -> GatewayConfig:
        api = self._get_api_or_404(cfg, api_id)
        self._get_tag_or_404(cfg, tag_id)
        self._link_list_item(api.tags, tag_id)
        return self.persist_or_apply_config(cfg)

    def unlink_api_tag(self, cfg: GatewayConfig, api_id: str, tag_id: str) -> GatewayConfig:
        api = self._get_api_or_404(cfg, api_id)
        if not self._unlink_list_item(api.tags, tag_id):
            raise HTTPException(status_code=404, detail="API tag link not found")
        return self.persist_or_apply_config(cfg)

    def upsert_api_operation(self, cfg: GatewayConfig, api_id: str, operation_id: str, body: Any) -> GatewayConfig:
        self.require_api_authoring_mode(cfg)
        api = self._get_api_or_404(cfg, api_id)
        self.validate_policy_xml(cfg, body.policies_xml)
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
        return self.persist_or_apply_config(cfg)

    def delete_api_operation(self, cfg: GatewayConfig, api_id: str, operation_id: str) -> GatewayConfig:
        self.require_api_authoring_mode(cfg)
        api = self._get_api_or_404(cfg, api_id)
        self._get_operation_or_404(cfg, api_id, operation_id)
        del api.operations[operation_id]
        return self.persist_or_apply_config(cfg)

    def link_operation_tag(self, cfg: GatewayConfig, api_id: str, operation_id: str, tag_id: str) -> GatewayConfig:
        operation = self._get_operation_or_404(cfg, api_id, operation_id)
        self._get_tag_or_404(cfg, tag_id)
        self._link_list_item(operation.tags, tag_id)
        return self.persist_or_apply_config(cfg)

    def unlink_operation_tag(self, cfg: GatewayConfig, api_id: str, operation_id: str, tag_id: str) -> GatewayConfig:
        operation = self._get_operation_or_404(cfg, api_id, operation_id)
        if not self._unlink_list_item(operation.tags, tag_id):
            raise HTTPException(status_code=404, detail="Operation tag link not found")
        return self.persist_or_apply_config(cfg)

    def link_product_group(self, cfg: GatewayConfig, product_id: str, group_id: str) -> GatewayConfig:
        product = self._get_product_or_404(cfg, product_id)
        self._get_group_or_404(cfg, group_id)
        self._link_list_item(product.groups, group_id)
        return self.persist_or_apply_config(cfg)

    def unlink_product_group(self, cfg: GatewayConfig, product_id: str, group_id: str) -> GatewayConfig:
        product = self._get_product_or_404(cfg, product_id)
        if not self._unlink_list_item(product.groups, group_id):
            raise HTTPException(status_code=404, detail="Product group link not found")
        return self.persist_or_apply_config(cfg)

    def link_product_tag(self, cfg: GatewayConfig, product_id: str, tag_id: str) -> GatewayConfig:
        product = self._get_product_or_404(cfg, product_id)
        self._get_tag_or_404(cfg, tag_id)
        self._link_list_item(product.tags, tag_id)
        return self.persist_or_apply_config(cfg)

    def unlink_product_tag(self, cfg: GatewayConfig, product_id: str, tag_id: str) -> GatewayConfig:
        product = self._get_product_or_404(cfg, product_id)
        if not self._unlink_list_item(product.tags, tag_id):
            raise HTTPException(status_code=404, detail="Product tag link not found")
        return self.persist_or_apply_config(cfg)

    def upsert_backend(self, cfg: GatewayConfig, backend_id: str, body: Any) -> GatewayConfig:
        payload = body.model_dump(mode="json") if hasattr(body, "model_dump") else dict(body)
        cfg.backends[backend_id] = BackendConfig(**payload)
        return self.persist_or_apply_config(cfg)

    def delete_backend(self, cfg: GatewayConfig, backend_id: str) -> GatewayConfig:
        self._get_backend_or_404(cfg, backend_id)
        del cfg.backends[backend_id]
        return self.persist_or_apply_config(cfg)

    def upsert_named_value(self, cfg: GatewayConfig, named_value_id: str, body: Any) -> GatewayConfig:
        payload = body.model_dump(mode="json") if hasattr(body, "model_dump") else dict(body)
        cfg.named_values[named_value_id] = NamedValueConfig(**payload)
        return self.persist_or_apply_config(cfg)

    def delete_named_value(self, cfg: GatewayConfig, named_value_id: str) -> GatewayConfig:
        self._get_named_value_or_404(cfg, named_value_id)
        del cfg.named_values[named_value_id]
        return self.persist_or_apply_config(cfg)

    def upsert_api_version_set(self, cfg: GatewayConfig, version_set_id: str, body: Any) -> GatewayConfig:
        self.require_api_authoring_mode(cfg)
        cfg.api_version_sets[version_set_id] = ApiVersionSetConfig(
            display_name=body.display_name,
            description=body.description,
            versioning_scheme=self.coerce_api_versioning_scheme(body.versioning_scheme),
            version_header_name=body.version_header_name,
            version_query_name=body.version_query_name,
            default_version=body.default_version,
        )
        return self.persist_or_apply_config(cfg)

    def delete_api_version_set(self, cfg: GatewayConfig, version_set_id: str) -> GatewayConfig:
        self.require_api_authoring_mode(cfg)
        version_set = cfg.api_version_sets.get(version_set_id)
        if version_set is None:
            raise HTTPException(status_code=404, detail="API version set not found")
        for api_id, api in cfg.apis.items():
            if api.api_version_set == version_set_id:
                raise HTTPException(status_code=409, detail=f"API version set is still in use by API {api_id}")
            for operation_id, operation in api.operations.items():
                if operation.api_version_set == version_set_id:
                    raise HTTPException(
                        status_code=409,
                        detail=f"API version set is still in use by operation {api_id}:{operation_id}",
                    )
        del cfg.api_version_sets[version_set_id]
        return self.persist_or_apply_config(cfg)

    def upsert_policy_fragment(self, cfg: GatewayConfig, fragment_id: str, xml: str) -> GatewayConfig:
        self.validate_fragment_xml(xml)
        cfg.policy_fragments[fragment_id] = xml
        return self.persist_or_apply_config(cfg)

    def delete_policy_fragment(self, cfg: GatewayConfig, fragment_id: str) -> GatewayConfig:
        if fragment_id not in cfg.policy_fragments:
            raise HTTPException(status_code=404, detail="Policy fragment not found")
        del cfg.policy_fragments[fragment_id]
        return self.persist_or_apply_config(cfg)

    def link_group_user(self, cfg: GatewayConfig, group_id: str, user_id: str) -> GatewayConfig:
        group = self._get_group_or_404(cfg, group_id)
        self._get_user_or_404(cfg, user_id)
        self._link_list_item(group.users, user_id)
        return self.persist_or_apply_config(cfg)

    def unlink_group_user(self, cfg: GatewayConfig, group_id: str, user_id: str) -> GatewayConfig:
        group = self._get_group_or_404(cfg, group_id)
        if not self._unlink_list_item(group.users, user_id):
            raise HTTPException(status_code=404, detail="Group user link not found")
        return self.persist_or_apply_config(cfg)

    def import_tofu_show(self, current: GatewayConfig, tf: dict[str, Any]) -> Any:
        result = import_from_tofu_show_json(tf)
        imported = result.config
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
        self.apply_runtime_config(imported)
        self.app.state.cache = {}
        self.app.state.policy_cache = {}
        self.app.state.policy_response_cache = {}
        self.app.state.policy_value_cache = {}
        self.app.state.rate_limit_store = {}
        self.app.state.quota_store = {}
        self.app.state.trace_store = {}
        return result

    def _get_api_or_404(self, cfg: GatewayConfig, api_id: str) -> ApiConfig:
        api = cfg.apis.get(api_id)
        if api is None:
            raise HTTPException(status_code=404, detail="API not found")
        return api

    def _get_operation_or_404(self, cfg: GatewayConfig, api_id: str, operation_id: str) -> OperationConfig:
        operation = self._get_api_or_404(cfg, api_id).operations.get(operation_id)
        if operation is None:
            raise HTTPException(status_code=404, detail="Operation not found")
        return operation

    def _get_api_revision_or_404(self, cfg: GatewayConfig, api_id: str, revision_id: str) -> ApiRevisionConfig:
        revision = self._get_api_or_404(cfg, api_id).revisions.get(revision_id)
        if revision is None:
            raise HTTPException(status_code=404, detail="API revision not found")
        return revision

    def _get_api_release_or_404(self, cfg: GatewayConfig, api_id: str, release_id: str):
        release = self._get_api_or_404(cfg, api_id).releases.get(release_id)
        if release is None:
            raise HTTPException(status_code=404, detail="API release not found")
        return release

    def _get_backend_or_404(self, cfg: GatewayConfig, backend_id: str) -> BackendConfig:
        backend = cfg.backends.get(backend_id)
        if backend is None:
            raise HTTPException(status_code=404, detail="Backend not found")
        return backend

    def _get_named_value_or_404(self, cfg: GatewayConfig, named_value_id: str) -> NamedValueConfig:
        named_value = cfg.named_values.get(named_value_id)
        if named_value is None:
            raise HTTPException(status_code=404, detail="Named value not found")
        return named_value

    def _set_current_revision(self, api: ApiConfig, revision_id: str, revision: ApiRevisionConfig) -> None:
        for candidate_id, candidate in api.revisions.items():
            candidate.is_current = candidate_id == revision_id
        api.revision = revision_id
        api.revision_description = revision.description
        api.source_api_id = revision.source_api_id
        api.is_current = True
        api.is_online = revision.is_online

    @staticmethod
    def _link_list_item(values: list[str], item_id: str) -> bool:
        if item_id in values:
            return False
        values.append(item_id)
        return True

    def _get_product_or_404(self, cfg: GatewayConfig, product_id: str) -> ProductConfig:
        product = cfg.products.get(product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="Product not found")
        return product

    def _get_group_or_404(self, cfg: GatewayConfig, group_id: str) -> GroupConfig:
        group = cfg.groups.get(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="Group not found")
        return group

    def _get_user_or_404(self, cfg: GatewayConfig, user_id: str) -> UserConfig:
        user = cfg.users.get(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        return user

    def _get_tag_or_404(self, cfg: GatewayConfig, tag_id: str) -> TagConfig:
        tag = cfg.tags.get(tag_id)
        if tag is None:
            raise HTTPException(status_code=404, detail="Tag not found")
        return tag

    @staticmethod
    def _unlink_list_item(values: list[str], item_id: str) -> bool:
        if item_id not in values:
            return False
        values[:] = [item for item in values if item != item_id]
        return True
