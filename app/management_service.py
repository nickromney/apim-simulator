from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from app.config import (
    DEFAULT_GLOBAL_POLICY_XML,
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
    ProductState,
    Subscription,
    SubscriptionKeyPair,
    SubscriptionScope,
    TagConfig,
    UserConfig,
    load_config,
    validate_policy_config,
)
from app.effective_policy import (
    EMPTY_POLICY_XML,
    effective_policy_xml,
    policy_xml_documents_for_target,
)
from app.named_values import validate_named_value_references
from app.openapi_import import parse_api_import
from app.policy import parse_policies_xml
from app.security import OIDCVerifier
from app.terraform_import import import_from_tofu_show_json

_REVISION_METADATA_FIELDS = {"revisions", "releases"}
_NONCURRENT_REVISION_IMMUTABLE_FIELDS = {"name", "path", "protocols", "api_version", "version_description"}


def _api_revision_definition(api: ApiConfig) -> dict[str, Any]:
    return api.model_dump(mode="python", exclude=_REVISION_METADATA_FIELDS)


def _validated_revision_definition(definition: dict[str, Any]) -> dict[str, Any]:
    try:
        api = ApiConfig.model_validate({**definition, "revisions": {}, "releases": {}})
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc
    return api.model_dump(mode="python", exclude=_REVISION_METADATA_FIELDS)


def _ensure_initial_api_revision(api: ApiConfig) -> None:
    """APIM creates revision 1 as the current revision when an API is added."""
    if api.revisions:
        return
    revision_id = api.revision or "1"
    api.revision = revision_id
    api.is_current = True
    if api.is_online is None:
        api.is_online = True
    api.revisions[revision_id] = ApiRevisionConfig(
        revision=revision_id,
        description=api.revision_description,
        is_current=True,
        is_online=api.is_online,
        source_api_id=api.source_api_id,
        definition=_api_revision_definition(api),
    )


def _sync_current_revision_definitions(cfg: GatewayConfig) -> None:
    """Keep the current revision URL aligned with direct edits to its API."""
    for api in cfg.apis.values():
        current_id = api.revision or next(
            (revision_id for revision_id, revision in api.revisions.items() if revision.is_current), None
        )
        current = api.revisions.get(current_id) if current_id else None
        if current is not None:
            current.definition = _api_revision_definition(api)


def _apply_revision_definition(api: ApiConfig, definition: dict[str, Any]) -> None:
    validated = ApiConfig.model_validate({**definition, "revisions": {}, "releases": {}})
    for field_name in ApiConfig.model_fields:
        if field_name not in _REVISION_METADATA_FIELDS:
            setattr(api, field_name, getattr(validated, field_name))


def _check_open_products(cfg: GatewayConfig, product_ids: set[str], scope: str) -> None:
    open_products = sorted(
        product_id
        for product_id in product_ids
        if product_id in cfg.products and not cfg.products[product_id].require_subscription
    )
    if len(open_products) > 1:
        raise ValueError(f"{scope} can belong to at most one open product; found {', '.join(open_products)}")


def _validate_open_product_associations(cfg: GatewayConfig) -> None:
    for api_id, api in cfg.apis.items():
        product_ids = set(api.products)
        for operation in api.operations.values():
            product_ids.update(operation.products or [])
        _check_open_products(cfg, product_ids, f"API {api_id}")
    for route in cfg.routes:
        if route.api_id in cfg.apis:
            continue
        _check_open_products(
            cfg, set(route.products) | ({route.product} if route.product else set()), f"Route {route.name}"
        )


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
        self._saved_config_path: str | None = None
        self._saved_config_digest: str | None = None

    def _prepare_config(self, cfg: GatewayConfig) -> tuple[GatewayConfig, dict[str, OIDCVerifier]]:
        from app.local_api_center import synchronize_api_center

        synchronize_api_center(cfg)
        cfg.routes = cfg.materialize_routes()
        validate_policy_config(cfg)
        return cfg, self._build_oidc_verifiers(cfg)

    def _publish_config(
        self,
        cfg: GatewayConfig,
        oidc_verifiers: dict[str, OIDCVerifier],
        *,
        clear_result_caches: bool,
    ) -> GatewayConfig:
        from app.security_monitoring import SecurityAuditSink

        current_sink = getattr(self.app.state, "security_audit_sink", None)
        if current_sink is None or current_sink.settings != cfg.security_observability:
            self.app.state.security_audit_sink = SecurityAuditSink(cfg.security_observability)
        self.app.state.gateway_config = cfg
        self.app.state.oidc_verifiers = oidc_verifiers
        self.app.state.policy_cache = {}
        if clear_result_caches:
            self.app.state.policy_response_cache = {}
            self.app.state.policy_value_cache = {}
        return cfg

    @staticmethod
    def _result_cache_inputs(cfg: GatewayConfig) -> tuple[Any, ...]:
        return (
            cfg.policies_xml,
            cfg.policies_xml_documents,
            cfg.policy_fragments,
            cfg.named_values,
            {key: value.policies_xml for key, value in cfg.products.items()},
            cfg.apis,
            cfg.routes,
            cfg.backends,
        )

    def consume_saved_config_fingerprint(self, path: Path) -> bool:
        """Skip the watcher reload caused by this service's own atomic save."""
        if self._saved_config_path != str(path) or self._saved_config_digest is None:
            return False
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return False
        if digest != self._saved_config_digest:
            return False
        self._saved_config_path = None
        self._saved_config_digest = None
        return True

    def reload_config(self) -> GatewayConfig:
        new_config = load_config()
        from app.security_governance import validate_governance_mutation

        validate_governance_mutation(self.app.state.gateway_config, new_config)
        new_config, oidc_verifiers = self._prepare_config(new_config)
        self._publish_config(new_config, oidc_verifiers, clear_result_caches=True)
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
        from app.security_governance import validate_governance_mutation

        validate_governance_mutation(getattr(self.app.state, "gateway_config", None), cfg)
        prepared, oidc_verifiers = self._prepare_config(cfg)
        return self._publish_config(prepared, oidc_verifiers, clear_result_caches=True)

    def _write_config_atomically(self, path: Path, payload: str) -> None:
        """Write and fsync beside the target, then atomically replace it."""
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
            ) as temporary:
                temporary_path = temporary.name
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass

    def persist_or_apply_config(self, cfg: GatewayConfig) -> GatewayConfig:
        from app.security_governance import validate_governance_mutation

        validate_governance_mutation(self.app.state.gateway_config, cfg)
        staged = cfg.model_copy(deep=True)
        _sync_current_revision_definitions(staged)
        try:
            _validate_open_product_associations(staged)
            staged, oidc_verifiers = self._prepare_config(staged)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid config update: {exc}") from exc
        config_path = os.getenv("APIM_CONFIG_PATH", "").strip()
        payload = self._serialize_gateway_config(staged) if config_path else None
        previous = getattr(self.app.state, "gateway_config", None)
        clear_result_caches = previous is None or self._result_cache_inputs(previous) != self._result_cache_inputs(
            staged
        )
        if config_path:
            self._saved_config_path = config_path
            self._saved_config_digest = hashlib.sha256((payload or "").encode("utf-8")).hexdigest()
            try:
                self._write_config_atomically(Path(config_path), payload or "")
            except OSError as exc:
                self._saved_config_path = None
                self._saved_config_digest = None
                raise HTTPException(status_code=500, detail="Unable to persist config update") from exc
            metrics = getattr(self.app.state, "gateway_metrics", None)
            if metrics is not None:
                metrics.config_reloads.add(1, {"result": "success"})
        self._publish_config(staged, oidc_verifiers, clear_result_caches=clear_result_caches)
        return staged

    def upsert_product(self, cfg: GatewayConfig, product_id: str, body: Any) -> GatewayConfig:
        existing = cfg.products.get(product_id)
        payload = existing.model_dump(mode="python") if existing is not None else {"state": ProductState.NotPublished}
        updates = body.model_dump(exclude_unset=True) if hasattr(body, "model_dump") else vars(body)
        try:
            product = ProductConfig.model_validate({**payload, **updates})
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid product configuration: {exc}") from exc
        cfg.products[product_id] = product
        return self.persist_or_apply_config(cfg)

    def delete_product(self, cfg: GatewayConfig, product_id: str) -> GatewayConfig:
        self._get_product_or_404(cfg, product_id)
        del cfg.products[product_id]
        for subscription in cfg.subscription.subscriptions.values():
            subscription.products = [item for item in subscription.products if item != product_id]
            if not subscription.products and subscription.scope == SubscriptionScope.Product:
                subscription.scope = None
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
        if getattr(body, "product_id", None) is not None and getattr(body, "products", None):
            raise HTTPException(status_code=400, detail="product_id conflicts with products")

        primary = body.primary_key or f"sub-{body.id}-primary"
        secondary = body.secondary_key or f"sub-{body.id}-secondary"
        try:
            cfg.subscription.subscriptions[body.id] = Subscription(
                id=body.id,
                name=body.name,
                keys=SubscriptionKeyPair(primary=primary, secondary=secondary),
                state=body.state,
                products=body.products if getattr(body, "product_id", None) is None else [body.product_id],
                scope=getattr(body, "scope", None),
                api_id=getattr(body, "api_id", None),
                all_apis=getattr(body, "all_apis", False),
                service_scoped=getattr(body, "service_scoped", False),
                created_by="management",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return self.persist_or_apply_config(cfg)

    @staticmethod
    def _validate_product_id_conflict(body: Any) -> None:
        if getattr(body, "product_id", None) is not None and getattr(body, "products", None):
            raise HTTPException(status_code=400, detail="product_id conflicts with products")

    @staticmethod
    def _subscription_scope_update(body: Any) -> dict[str, Any] | None:
        fields = ("products", "product_id", "scope", "api_id", "all_apis", "service_scoped")
        if not any(getattr(body, field, None) is not None for field in fields):
            return None
        values: dict[str, Any] = {
            "products": [],
            "scope": None,
            "api_id": None,
            "all_apis": False,
            "service_scoped": False,
        }
        if getattr(body, "products", None) is not None:
            values["products"] = body.products
        if getattr(body, "product_id", None) is not None:
            values["products"] = [body.product_id]
        if getattr(body, "api_id", None) is not None:
            values["api_id"] = body.api_id
        if getattr(body, "all_apis", None) is True:
            values["all_apis"] = True
        if getattr(body, "service_scoped", None) is True:
            values["service_scoped"] = True
        if getattr(body, "scope", None) is not None:
            values["scope"] = body.scope
        return values

    def update_subscription(self, cfg: GatewayConfig, subscription_id: str, body: Any) -> GatewayConfig:
        sub = self.find_subscription_by_id(cfg, subscription_id)
        if sub is None:
            raise HTTPException(status_code=404, detail="Subscription not found")

        if body.name is not None:
            sub.name = body.name
        if body.state is not None:
            sub.state = body.state
        self._validate_product_id_conflict(body)
        scope_values = self._subscription_scope_update(body)
        if scope_values is not None:
            try:
                replacement = Subscription.model_validate(
                    {
                        **sub.model_dump(mode="python"),
                        **scope_values,
                    }
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            sub.products = replacement.products
            sub.scope = replacement.scope
            sub.api_id = replacement.api_id
            sub.all_apis = replacement.all_apis
            sub.service_scoped = replacement.service_scoped
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
        try:
            parse_policies_xml(
                xml.strip() or EMPTY_POLICY_XML,
                policy_fragments=cfg.policy_fragments,
                gateway_config=cfg,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except HTTPException as exc:
            raise HTTPException(status_code=400, detail=exc.detail) from exc

    def validate_fragment_xml(self, cfg: GatewayConfig, xml: str) -> None:
        from app.policy_fragments import validate_fragment_xml

        try:
            validate_fragment_xml(xml)
            validate_named_value_references(xml, cfg)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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

    def _api_scope(self, cfg: GatewayConfig, scope_name: str) -> Any:
        api = cfg.apis.get(scope_name)
        if api is None:
            raise HTTPException(status_code=404, detail="API policy scope not found")
        return api

    def _product_scope(self, cfg: GatewayConfig, scope_name: str) -> Any:
        product = cfg.products.get(scope_name)
        if product is None:
            raise HTTPException(status_code=404, detail="Product policy scope not found")
        return product

    def _operation_scope(self, cfg: GatewayConfig, scope_name: str) -> Any:
        api_name, sep, operation_name = scope_name.partition(":")
        if not sep:
            raise HTTPException(status_code=400, detail="Operation scope must use api:operation")
        operation = self._api_scope(cfg, api_name).operations.get(operation_name)
        if operation is None:
            raise HTTPException(status_code=404, detail="Operation policy scope not found")
        return operation

    def _route_scope(self, cfg: GatewayConfig, scope_name: str) -> Any:
        """Routes are only addressable in a config that declares no APIs.

        Where APIs exist the routes are derived from them, so writing policy to
        one would be silently discarded on the next materialisation.
        """
        if cfg.apis:
            raise HTTPException(status_code=400, detail="Route policy updates are unavailable for API-backed configs")
        for route in cfg.routes:
            if route.name == scope_name:
                return route
        raise HTTPException(status_code=404, detail="Route policy scope not found")

    def policy_scope_target(self, cfg: GatewayConfig, scope_type: str, scope_name: str) -> Any:
        """The object a policy document at this scope is attached to."""
        scope = scope_type.lower()
        if scope == "gateway":
            return cfg
        resolvers = {
            "api": self._api_scope,
            "product": self._product_scope,
            "operation": self._operation_scope,
            "route": self._route_scope,
        }
        resolver = resolvers.get(scope)
        if resolver is None:
            raise HTTPException(status_code=404, detail="Unsupported policy scope")
        return resolver(cfg, scope_name)

    def policy_xml_for_target(self, target: Any) -> str:
        documents = policy_xml_documents_for_target(target)
        if not documents and isinstance(target, GatewayConfig):
            # Reading then saving the implicit global document must preserve
            # the backend forwarding policy that runtime inheritance uses.
            return DEFAULT_GLOBAL_POLICY_XML
        if len(documents) == 1:
            return documents[0]
        return effective_policy_xml(*([document] for document in documents))

    def set_policy_xml(self, target: Any, xml: str) -> None:
        target.policies_xml = xml
        if hasattr(target, "policies_xml_documents"):
            target.policies_xml_documents = []

    def put_policy(self, cfg: GatewayConfig, scope_type: str, scope_name: str, xml: str) -> GatewayConfig:
        cleaned = xml.strip() or EMPTY_POLICY_XML
        self.validate_policy_xml(cfg, cleaned)
        target = self.policy_scope_target(cfg, scope_type, scope_name)
        self.set_policy_xml(target, cleaned)
        return self.persist_or_apply_config(cfg)

    def import_api(self, cfg: GatewayConfig, api_id: str, body: Any) -> tuple[GatewayConfig, Any]:
        self.require_api_authoring_mode(cfg)
        self.validate_policy_xml(cfg, body.policies_xml)
        try:
            imported = parse_api_import(
                content_format=body.content_format,
                content_value=body.content_value,
                translate_required_query_parameters=body.translate_required_query_parameters,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        existing = cfg.apis.get(api_id)
        upstream_base_url = (
            body.upstream_base_url if body.upstream_base_url is not None else (imported.upstream_base_url or "")
        )

        operations: dict[str, OperationConfig] = {}
        existing_operations = existing.operations if existing is not None else {}
        used_operation_ids: set[str] = set()
        for imported_operation in imported.operations:
            matched_id = imported_operation.source_operation_id
            preserved = existing_operations.get(matched_id or "") if existing is not None else None
            operation_id = (
                imported_operation.name
                if existing is None
                else (matched_id if preserved is not None else imported_operation.generated_name)
            )
            preserved = preserved or next(
                (
                    item
                    for item in existing_operations.values()
                    if item.method.upper() == imported_operation.method
                    and item.url_template.split("?", maxsplit=1)[0]
                    == imported_operation.url_template.split("?", maxsplit=1)[0]
                ),
                None,
            )
            operation_id = operation_id or imported_operation.name
            base_id = operation_id
            suffix = 1
            while operation_id in used_operation_ids:
                tail = f"-{suffix}"
                operation_id = f"{base_id[:76].rstrip('-')}{tail}"
                suffix += 1
            operations[operation_id] = OperationConfig(
                name=imported_operation.display_name,
                method=imported_operation.method,
                url_template=imported_operation.url_template,
                description=imported_operation.description,
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
                template_parameters=imported_operation.template_parameters,
                request=imported_operation.request,
                responses=imported_operation.responses,
            )
            used_operation_ids.add(operation_id)

        cfg.apis[api_id] = ApiConfig(
            name=body.name or (existing.name if existing is not None else api_id),
            path=body.path or (existing.path if existing is not None else api_id),
            upstream_base_url=upstream_base_url,
            upstream_path_prefix=body.upstream_path_prefix,
            protocols=(
                body.protocols
                if body.protocols is not None
                else (existing.protocols if existing is not None else ["http", "https"])
            ),
            translate_required_query_parameters=body.translate_required_query_parameters,
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
            schemas=imported.schemas,
            revisions=existing.revisions if existing is not None else {},
            releases=existing.releases if existing is not None else {},
        )
        if existing is None:
            _ensure_initial_api_revision(cfg.apis[api_id])
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
            protocols=body.protocols,
            translate_required_query_parameters=body.translate_required_query_parameters,
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
        if existing is None:
            _ensure_initial_api_revision(cfg.apis[api_id])
        return self.persist_or_apply_config(cfg)

    def delete_api(self, cfg: GatewayConfig, api_id: str) -> GatewayConfig:
        self.require_api_authoring_mode(cfg)
        self._get_api_or_404(cfg, api_id)
        del cfg.apis[api_id]
        # Materialized routes belong to the deleted catalog, not legacy input.
        if not cfg.apis:
            cfg.routes = []
        return self.persist_or_apply_config(cfg)

    def upsert_api_revision(self, cfg: GatewayConfig, api_id: str, revision_id: str, body: Any) -> GatewayConfig:
        self.require_api_authoring_mode(cfg)
        api = self._get_api_or_404(cfg, api_id)
        if not api.revisions:
            _ensure_initial_api_revision(api)
        existing = api.revisions.get(revision_id)
        definition = (
            dict(existing.definition) if existing is not None and existing.definition else _api_revision_definition(api)
        )
        if body.definition is not None:
            definition.update(body.definition)
        current_id = api.revision or next(
            (candidate_id for candidate_id, candidate in api.revisions.items() if candidate.is_current), None
        )
        if current_id is not None and revision_id != current_id:
            changed_fields = [
                field for field in _NONCURRENT_REVISION_IMMUTABLE_FIELDS if definition.get(field) != getattr(api, field)
            ]
            if changed_fields:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot change {', '.join(sorted(changed_fields))} on a non-current API revision",
                )
        # Validate the complete snapshot before storing it, so a malformed
        # revision cannot poison route materialization for every request.
        validated_definition = _validated_revision_definition(definition)
        validated_api = ApiConfig.model_validate({**validated_definition, "revisions": {}, "releases": {}})
        self.validate_policy_xml(cfg, validated_api.policies_xml)
        for operation in validated_api.operations.values():
            self.validate_policy_xml(cfg, operation.policies_xml)
        revision = ApiRevisionConfig(
            revision=revision_id,
            description=body.description
            if body.description is not None
            else (existing.description if existing else None),
            is_current=(
                body.is_current
                if body.is_current is not None
                else (
                    existing.is_current
                    if existing is not None and existing.is_current is not None
                    else revision_id == current_id
                )
            ),
            is_online=(
                body.is_online
                if body.is_online is not None
                else (existing.is_online if existing is not None and existing.is_online is not None else True)
            ),
            source_api_id=(
                body.source_api_id if body.source_api_id is not None else (existing.source_api_id if existing else None)
            ),
            definition=validated_definition,
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
        # In APIM, creating a release is the operation that promotes the chosen
        # revision and optionally publishes its change-log note.
        self._set_current_revision(api, body.revision, api.revisions[body.revision])
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
        changes = body.model_dump(mode="json", exclude_unset=True) if hasattr(body, "model_dump") else dict(body)
        from app.named_values import rename_named_value_references

        existing = cfg.named_values.get(named_value_id)
        payload = {**(existing.model_dump(mode="json") if existing else {}), **changes}
        if changes.get("value_from_key_vault") is not None:
            payload["value"] = None
        elif changes.get("value") is not None:
            payload["value_from_key_vault"] = None
            if existing and existing.value_from_key_vault and "secret" not in changes:
                payload["secret"] = True
        new_name = payload.get("display_name") or (existing.display_name if existing else None) or named_value_id
        for identifier, entry in cfg.named_values.items():
            if identifier != named_value_id and new_name in {identifier, entry.display_name}:
                raise HTTPException(status_code=400, detail="Named value display name is already in use")
        payload["display_name"] = new_name
        old_name = (existing.display_name or named_value_id) if existing else new_name
        cfg.named_values[named_value_id] = NamedValueConfig(**payload)
        if old_name != new_name:
            cfg = rename_named_value_references(cfg, old_name, new_name)
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
        self.validate_fragment_xml(cfg, xml)
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
        try:
            result = import_from_tofu_show_json(tf)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        imported = result.config
        imported.allowed_origins = current.allowed_origins
        imported.allow_anonymous = current.allow_anonymous
        imported.oidc = current.oidc
        imported.oidc_providers = current.oidc_providers
        imported.admin_token = current.admin_token
        imported.tenant_access = current.tenant_access
        imported.trace_enabled = current.trace_enabled
        for field in (
            "trace_allow_unauthenticated",
            "network_security",
            "certificates",
            "workload_identity",
            "control_plane",
            "secret_storage",
            "security_governance",
            "security_ingress",
            "security_observability",
            "portal",
            "local_messaging",
        ):
            setattr(imported, field, getattr(current.model_copy(deep=True), field))
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
        definition = _validated_revision_definition(revision.definition or _api_revision_definition(api))
        _apply_revision_definition(api, definition)
        for candidate_id, candidate in api.revisions.items():
            candidate.is_current = candidate_id == revision_id
        revision.is_current = True
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
