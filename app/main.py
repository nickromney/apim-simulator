from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from app.config import GatewayConfig, Subscription, SubscriptionState, load_config
from app.named_values import mask_secret_data
from app.policy import (
    PolicyRequest,
    PolicyRuntime,
    PolicyTraceCollector,
    apply_backend_async,
    apply_inbound_async,
    apply_on_error_async,
    apply_outbound_async,
    finalize_deferred_actions,
    parse_policies_xml,
)
from app.proxy import build_upstream_headers, build_user_payload, filter_response_headers, resolve_route
from app.security import (
    OIDCVerifier,
    authenticate_request,
    build_client_principal,
    subscription_bypassed,
    validate_client_certificate,
)
from app.terraform_import import import_from_tofu_show_json

logging.basicConfig(level=logging.INFO)
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


def _serialize_gateway_config(cfg: GatewayConfig) -> str:
    payload = cfg.model_dump(mode="json")
    if payload.get("apis"):
        payload["routes"] = []
    return json.dumps(payload, indent=2) + "\n"


def _decode_body(content: bytes) -> dict[str, str | None]:
    if not content:
        return {"text": "", "base64": None}
    try:
        return {"text": content.decode("utf-8"), "base64": None}
    except UnicodeDecodeError:
        return {"text": None, "base64": base64.b64encode(content).decode("ascii")}


def _apply_claim_headers(headers: dict[str, str], claims: dict[str, Any]) -> None:
    headers["x-apim-user-object-id"] = str(claims.get("sub", ""))
    headers["x-apim-user-email"] = str(claims.get("email", ""))
    headers["x-apim-user-name"] = str(claims.get("name") or claims.get("preferred_username") or "")
    headers["x-apim-auth-method"] = "oidc"
    headers["x-ms-client-principal"] = build_client_principal(claims)
    headers["x-ms-client-principal-name"] = str(claims.get("preferred_username", ""))


def _trace_payload(
    *,
    trace_base: dict[str, Any],
    trace_collector: PolicyTraceCollector | None,
    cfg: GatewayConfig,
    extra: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        **trace_base,
        "policy_steps": trace_collector.steps if trace_collector else [],
        "policy_variable_writes": trace_collector.variable_writes if trace_collector else [],
        "jwt_validations": trace_collector.jwt_validations if trace_collector else [],
        "send_requests": trace_collector.send_requests if trace_collector else [],
        "selected_backend": trace_collector.selected_backend if trace_collector else None,
        **extra,
    }
    return mask_secret_data(payload, cfg)


def _render_backend_value(value: str | None, policy_req: PolicyRequest, cfg: GatewayConfig) -> str | None:
    if value is None:
        return None
    runtime = PolicyRuntime(gateway_config=cfg)
    from app.policy import render_policy_value

    return render_policy_value(value, policy_req, runtime)


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


class PolicyUpdate(BaseModel):
    xml: str


class ReplayRequestBody(BaseModel):
    method: str = "GET"
    path: str
    query: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body_text: str | None = None
    body_base64: str | None = None


def create_app(*, config: GatewayConfig | None = None, http_client: httpx.AsyncClient | None = None) -> FastAPI:
    gateway_config = config or load_config()
    gateway_config.routes = gateway_config.materialize_routes()

    def _build_oidc_verifiers(cfg: GatewayConfig) -> dict[str, OIDCVerifier]:
        verifiers: dict[str, OIDCVerifier] = {}
        if cfg.oidc_providers:
            for provider_id, provider in cfg.oidc_providers.items():
                verifiers[provider_id] = OIDCVerifier(
                    provider.issuer,
                    provider.audience,
                    jwks_uri=provider.jwks_uri,
                    jwks=provider.jwks,
                )
        elif cfg.oidc is not None:
            verifiers["default"] = OIDCVerifier(
                cfg.oidc.issuer,
                cfg.oidc.audience,
                jwks_uri=cfg.oidc.jwks_uri,
                jwks=cfg.oidc.jwks,
            )
        return verifiers

    oidc_verifiers = _build_oidc_verifiers(gateway_config)

    def _reload_config(app: FastAPI) -> GatewayConfig:
        """Reload configuration from file and update app state."""
        new_config = load_config()
        new_config.routes = new_config.materialize_routes()
        new_verifiers = _build_oidc_verifiers(new_config)
        app.state.gateway_config = new_config
        app.state.oidc_verifiers = new_verifiers
        app.state.policy_cache = {}  # Clear policy cache on reload
        app.state.policy_response_cache = {}
        app.state.policy_value_cache = {}
        logger.info(
            "config reloaded | routes=%d | origins=%s | anonymous=%s",
            len(new_config.routes),
            new_config.allowed_origins,
            new_config.allow_anonymous,
        )
        return new_config

    async def _config_watcher(app: FastAPI, config_path: str, interval: float = 5.0) -> None:
        """Watch config file for changes and reload when modified.

        Kubernetes ConfigMaps are mounted as symlinks that change on update.
        We track both mtime and resolved symlink target to detect changes.
        """
        path = Path(config_path)
        last_mtime: float = 0
        last_target: str = ""

        try:
            if path.exists():
                last_mtime = path.stat().st_mtime
                last_target = str(path.resolve()) if path.is_symlink() else ""
        except OSError:
            pass

        logger.info("config watcher started | path=%s | interval=%.1fs", config_path, interval)

        while True:
            await asyncio.sleep(interval)
            try:
                if not path.exists():
                    continue

                current_mtime = path.stat().st_mtime
                current_target = str(path.resolve()) if path.is_symlink() else ""

                changed = False
                if current_mtime != last_mtime:
                    changed = True
                    last_mtime = current_mtime
                if current_target and current_target != last_target:
                    changed = True
                    last_target = current_target

                if changed:
                    logger.info("config file changed, reloading...")
                    _reload_config(app)
            except Exception as exc:
                logger.warning("config watcher error: %s", exc)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        created = False
        if http_client is None:
            app.state.http_client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
            created = True
        else:
            app.state.http_client = http_client
        app.state.gateway_config = gateway_config
        app.state.oidc_verifiers = oidc_verifiers
        app.state.cache = {}
        app.state.policy_cache = {}
        app.state.policy_response_cache = {}
        app.state.policy_value_cache = {}
        app.state.rate_limit_store = {}
        app.state.quota_store = {}
        app.state.trace_store = {}
        app.state.config_reload_fn = lambda: _reload_config(app)
        app.state.startup_complete = True

        watcher_task: asyncio.Task | None = None
        config_path = os.getenv("APIM_CONFIG_PATH", "").strip()
        watch_enabled = os.getenv("APIM_CONFIG_WATCH", "false").lower() == "true"
        watch_interval = float(os.getenv("APIM_CONFIG_WATCH_INTERVAL", "5"))

        if config_path and watch_enabled:
            watcher_task = asyncio.create_task(_config_watcher(app, config_path, watch_interval))

        logger.info(
            "apim-sim ready | routes=%d | origins=%s | anonymous=%s | watch=%s",
            len(gateway_config.routes),
            gateway_config.allowed_origins,
            gateway_config.allow_anonymous,
            watch_enabled,
        )
        yield
        if watcher_task:
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass
        if created:
            await app.state.http_client.aclose()

    app = FastAPI(title="Local APIM Simulator", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=gateway_config.allowed_origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/")
    async def root_hint(request: Request) -> dict[str, Any]:
        cfg: GatewayConfig = request.app.state.gateway_config
        route_prefixes = sorted({route.path_prefix or "/" for route in cfg.routes})
        return {
            "service": "Local APIM Simulator",
            "message": "This is an API gateway. Try /apim/health, /apim/startup, or one of the configured route prefixes.",
            "gateway_endpoints": ["/apim/health", "/apim/startup"],
            "route_prefixes": route_prefixes,
            "management": {
                "enabled": cfg.tenant_access.enabled,
                "status_path": "/apim/management/status" if cfg.tenant_access.enabled else None,
                "required_header": "X-Apim-Tenant-Key" if cfg.tenant_access.enabled else None,
            },
            "operator_console": {
                "url": "http://localhost:3007",
                "note": "Run make up-ui to start the operator console.",
            },
        }

    @app.get("/apim/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    @app.get("/apim/startup")
    async def startup(request: Request) -> dict[str, str]:
        """Startup probe endpoint - returns 200 once app is ready to serve traffic."""
        if not getattr(request.app.state, "startup_complete", False):
            raise HTTPException(status_code=503, detail="Starting up")
        return {"status": "started"}

    @app.post("/apim/reload")
    async def reload_config(request: Request) -> dict[str, Any]:
        """Reload configuration from file. Requires admin token if configured."""
        cfg: GatewayConfig = request.app.state.gateway_config
        if cfg.admin_token:
            _require_admin(request)
        reload_fn = getattr(request.app.state, "config_reload_fn", None)
        if reload_fn is None:
            raise HTTPException(status_code=500, detail="Reload not available")
        new_cfg = reload_fn()
        return {
            "status": "reloaded",
            "routes": len(new_cfg.routes),
            "products": len(new_cfg.products),
            "subscriptions": len(new_cfg.subscription.subscriptions),
        }

    @app.get("/apim/trace/{trace_id}")
    async def get_trace(trace_id: str, request: Request) -> dict[str, Any]:
        cfg: GatewayConfig = request.app.state.gateway_config
        if not cfg.trace_enabled:
            raise HTTPException(status_code=404, detail="Not found")
        if cfg.admin_token:
            _require_admin(request)

        trace_store: dict[str, Any] = request.app.state.trace_store
        entry = trace_store.get(trace_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="Not found")
        return entry

    @app.get("/apim/user")
    async def current_user(request: Request) -> dict:
        cfg: GatewayConfig = request.app.state.gateway_config
        verifiers: dict[str, OIDCVerifier] = request.app.state.oidc_verifiers
        auth = authenticate_request(request, cfg, verifiers)
        return build_user_payload(auth, None, None)

    def _extract_scopes(claims: dict) -> set[str]:
        scopes: set[str] = set()
        raw = claims.get("scope") or claims.get("scp")
        if isinstance(raw, str):
            scopes.update(s for s in raw.split() if s)
        if isinstance(raw, list):
            scopes.update(str(s) for s in raw if s)
        return scopes

    def _extract_roles(claims: dict) -> set[str]:
        roles: set[str] = set()
        raw = claims.get("roles")
        if isinstance(raw, str) and raw:
            roles.add(raw)
        if isinstance(raw, list):
            roles.update(str(r) for r in raw if r)

        realm_access = claims.get("realm_access")
        if isinstance(realm_access, dict):
            rr = realm_access.get("roles")
            if isinstance(rr, list):
                roles.update(str(r) for r in rr if r)

        # Keycloak client roles typically live under resource_access.{client}.roles.
        resource_access = claims.get("resource_access")
        if isinstance(resource_access, dict):
            for entry in resource_access.values():
                if not isinstance(entry, dict):
                    continue
                cr = entry.get("roles")
                if isinstance(cr, list):
                    roles.update(str(r) for r in cr if r)
        return roles

    def _require_admin(request: Request) -> None:
        cfg: GatewayConfig = request.app.state.gateway_config
        if not cfg.admin_token:
            raise HTTPException(status_code=404, detail="Not found")
        provided = request.headers.get("x-apim-admin-token", "")
        if provided != cfg.admin_token:
            raise HTTPException(status_code=403, detail="Forbidden")

    def _require_tenant_access(request: Request) -> None:
        cfg: GatewayConfig = request.app.state.gateway_config
        if not cfg.tenant_access.enabled:
            raise HTTPException(status_code=404, detail="Not found")

        # Allow admin token as a super-user escape hatch for local dev.
        admin = request.headers.get("x-apim-admin-token", "")
        if cfg.admin_token and admin == cfg.admin_token:
            return

        provided = request.headers.get("x-apim-tenant-key", "")
        if not provided:
            raise HTTPException(status_code=403, detail="Forbidden")

        if provided == (cfg.tenant_access.primary_key or ""):
            return
        if provided == (cfg.tenant_access.secondary_key or ""):
            return
        raise HTTPException(status_code=403, detail="Forbidden")

    def _find_subscription_by_id(cfg: GatewayConfig, subscription_id: str) -> Subscription | None:
        for sub in cfg.subscription.subscriptions.values():
            if sub.id == subscription_id:
                return sub
        return None

    @app.post("/apim/admin/subscriptions/{subscription_id}/rotate")
    async def rotate_subscription_key(subscription_id: str, request: Request, key: str = "secondary") -> dict:
        _require_admin(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        sub = _find_subscription_by_id(cfg, subscription_id)
        if sub is None:
            raise HTTPException(status_code=404, detail="Subscription not found")
        if key not in {"primary", "secondary"}:
            raise HTTPException(status_code=400, detail="Invalid key")

        # Keep this deterministic (non-secret) so we don't accidentally commit real keys.
        new_key = f"rotated-{sub.id}-{key}"
        if key == "primary":
            sub.keys.primary = new_key
        else:
            sub.keys.secondary = new_key
        return {"subscription_id": sub.id, "subscription_name": sub.name, "rotated": key, "new_key": new_key}

    def _apply_runtime_config(app: FastAPI, cfg: GatewayConfig) -> GatewayConfig:
        cfg.routes = cfg.materialize_routes()
        app.state.gateway_config = cfg
        app.state.oidc_verifiers = _build_oidc_verifiers(cfg)
        app.state.policy_cache = {}
        app.state.policy_response_cache = {}
        app.state.policy_value_cache = {}
        return cfg

    def _persist_or_apply_config(request: Request, cfg: GatewayConfig) -> GatewayConfig:
        config_path = os.getenv("APIM_CONFIG_PATH", "").strip()
        if not config_path:
            return _apply_runtime_config(request.app, cfg)

        try:
            Path(config_path).write_text(_serialize_gateway_config(cfg), encoding="utf-8")
        except OSError as exc:
            raise HTTPException(status_code=500, detail="Unable to persist config update") from exc

        reload_fn = getattr(request.app.state, "config_reload_fn", None)
        if reload_fn is None:
            raise HTTPException(status_code=500, detail="Reload not available")
        return reload_fn()

    def _policy_scope_target(cfg: GatewayConfig, scope_type: str, scope_name: str) -> Any:
        scope = scope_type.lower()
        if scope == "gateway":
            return cfg
        if scope == "api":
            api = cfg.apis.get(scope_name)
            if api is None:
                raise HTTPException(status_code=404, detail="API policy scope not found")
            return api
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

    def _summary_payload(cfg: GatewayConfig) -> dict[str, Any]:
        apis: list[dict[str, Any]] = []
        for api_id, api in cfg.apis.items():
            operations: list[dict[str, Any]] = []
            for operation_id, operation in api.operations.items():
                operations.append(
                    {
                        "id": operation_id,
                        "name": operation.name,
                        "method": operation.method,
                        "url_template": operation.url_template,
                        "upstream_base_url": operation.upstream_base_url,
                        "upstream_path_prefix": operation.upstream_path_prefix,
                        "backend": operation.backend,
                        "products": operation.products,
                        "policy_scope": {"scope_type": "operation", "scope_name": f"{api_id}:{operation_id}"},
                    }
                )
            apis.append(
                {
                    "id": api_id,
                    "name": api.name,
                    "path": api.path,
                    "upstream_base_url": api.upstream_base_url,
                    "upstream_path_prefix": api.upstream_path_prefix,
                    "backend": api.backend,
                    "products": api.products,
                    "policy_scope": {"scope_type": "api", "scope_name": api_id},
                    "operations": operations,
                }
            )

        routes: list[dict[str, Any]] = []
        for route in cfg.routes:
            route_payload: dict[str, Any] = {
                "name": route.name,
                "path_prefix": route.path_prefix,
                "host_match": route.host_match,
                "methods": route.methods,
                "upstream_base_url": route.upstream_base_url,
                "upstream_path_prefix": route.upstream_path_prefix,
                "backend": route.backend,
                "product": route.product,
                "products": route.products,
                "api_version_set": route.api_version_set,
                "api_version": route.api_version,
            }
            if not cfg.apis:
                route_payload["policy_scope"] = {"scope_type": "route", "scope_name": route.name}
            routes.append(route_payload)

        return {
            "gateway_policy_scope": {"scope_type": "gateway", "scope_name": "gateway"},
            "apis": apis,
            "routes": routes,
            "products": [{"id": product_id, **product.model_dump()} for product_id, product in cfg.products.items()],
            "subscriptions": [sub.model_dump() for sub in cfg.subscription.subscriptions.values()],
            "backends": [{"id": backend_id, **backend.model_dump()} for backend_id, backend in cfg.backends.items()],
        }

    @app.get("/apim/management/status")
    async def management_status(request: Request) -> dict:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        return {
            "routes": len(cfg.routes),
            "products": len(cfg.products),
            "subscriptions": len(cfg.subscription.subscriptions),
            "api_version_sets": len(cfg.api_version_sets),
        }

    @app.get("/apim/management/summary")
    async def management_summary(request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        return _summary_payload(cfg)

    @app.get("/apim/management/policies/{scope_type}/{scope_name:path}")
    async def management_get_policy(scope_type: str, scope_name: str, request: Request) -> dict[str, Any]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        target = _policy_scope_target(cfg, scope_type, scope_name)
        return {
            "scope_type": scope_type,
            "scope_name": scope_name,
            "xml": _policy_xml_for_target(target),
        }

    @app.put("/apim/management/policies/{scope_type}/{scope_name:path}")
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

    @app.get("/apim/management/traces")
    async def management_traces(request: Request, limit: int = 50) -> dict[str, Any]:
        _require_tenant_access(request)
        trace_store: dict[str, Any] = request.app.state.trace_store
        items = sorted(trace_store.values(), key=lambda item: item.get("created_at", ""), reverse=True)
        return {"items": items[: max(1, min(limit, 200))]}

    @app.post("/apim/management/replay")
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
        async with httpx.AsyncClient(transport=transport, base_url="http://apim-replay.local") as replay_client:
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

    @app.get("/apim/management/subscriptions")
    async def list_subscriptions(request: Request) -> list[dict]:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        return [sub.model_dump() for sub in cfg.subscription.subscriptions.values()]

    @app.post("/apim/management/subscriptions")
    async def create_subscription(request: Request, body: SubscriptionUpsert) -> dict:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        if _find_subscription_by_id(cfg, body.id) is not None:
            raise HTTPException(status_code=409, detail="Subscription already exists")

        primary = body.primary_key or f"sub-{body.id}-primary"
        secondary = body.secondary_key or f"sub-{body.id}-secondary"
        sub = Subscription(
            id=body.id,
            name=body.name,
            keys={"primary": primary, "secondary": secondary},
            state=body.state,
            products=body.products,
            created_by="management",
        )
        cfg.subscription.subscriptions[body.id] = sub
        return sub.model_dump()

    @app.patch("/apim/management/subscriptions/{subscription_id}")
    async def update_subscription(request: Request, subscription_id: str, body: SubscriptionUpdate) -> dict:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        sub = _find_subscription_by_id(cfg, subscription_id)
        if sub is None:
            raise HTTPException(status_code=404, detail="Subscription not found")

        if body.name is not None:
            sub.name = body.name
        if body.state is not None:
            sub.state = body.state
        if body.products is not None:
            sub.products = body.products
        return sub.model_dump()

    @app.post("/apim/management/subscriptions/{subscription_id}/rotate")
    async def management_rotate_subscription_key(
        subscription_id: str, request: Request, key: str = "secondary"
    ) -> dict:
        _require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        sub = _find_subscription_by_id(cfg, subscription_id)
        if sub is None:
            raise HTTPException(status_code=404, detail="Subscription not found")
        if key not in {"primary", "secondary"}:
            raise HTTPException(status_code=400, detail="Invalid key")

        new_key = f"rotated-{sub.id}-{key}"
        if key == "primary":
            sub.keys.primary = new_key
        else:
            sub.keys.secondary = new_key
        return {"subscription_id": sub.id, "subscription_name": sub.name, "rotated": key, "new_key": new_key}

    @app.post("/apim/management/import/tofu-show")
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

        imported.routes = imported.materialize_routes()
        request.app.state.gateway_config = imported
        request.app.state.oidc_verifiers = _build_oidc_verifiers(imported)
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
            "subscriptions": len(imported.subscription.subscriptions),
            "apis": len(imported.apis),
            "diagnostics": [item.__dict__ for item in result.diagnostics],
        }

    @app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def gateway_proxy(full_path: str, request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204)

        cfg: GatewayConfig = request.app.state.gateway_config

        # mTLS validation (before route resolution)
        validate_client_certificate(request, cfg)

        resolved = resolve_route(cfg, request)
        if resolved is None:
            raise HTTPException(status_code=404, detail="No route")
        route = resolved.route

        verifiers: dict[str, OIDCVerifier] = request.app.state.oidc_verifiers
        auth = authenticate_request(request, cfg, verifiers, route)

        if route.product:
            allowed_products = [route.product]
        else:
            allowed_products = []

        if route.products:
            allowed_products = list(route.products)

        if allowed_products:
            require_sub = any(
                (cfg.products.get(p).require_subscription if cfg.products.get(p) else True) for p in allowed_products
            )
            if require_sub and subscription_bypassed(request, cfg):
                require_sub = False
            if require_sub:
                if auth.subscription is None:
                    raise HTTPException(status_code=401, detail="Missing subscription key")
                if not set(allowed_products).intersection(set(auth.subscription_products)):
                    raise HTTPException(status_code=403, detail="Subscription not authorized for product")

        policy_docs: list[Any] = []
        policy_cache: dict[str, Any] = request.app.state.policy_cache

        def _doc_for(xml: str) -> Any:
            cache_key = (xml, tuple(sorted(cfg.policy_fragments.items())))
            cached = policy_cache.get(cache_key)
            if cached is not None:
                return cached
            doc = parse_policies_xml(xml, policy_fragments=cfg.policy_fragments)
            policy_cache[cache_key] = doc
            return doc

        for xml in cfg.policies_xml_documents:
            policy_docs.append(_doc_for(xml))
        if cfg.policies_xml:
            policy_docs.append(_doc_for(cfg.policies_xml))
        for xml in route.policies_xml_documents:
            policy_docs.append(_doc_for(xml))
        if route.policies_xml:
            policy_docs.append(_doc_for(route.policies_xml))

        body = await request.body()
        if len(body) > cfg.max_request_body_bytes:
            raise HTTPException(status_code=413, detail="Request body too large")
        headers = {k.lower(): v for k, v in build_upstream_headers(request, auth).items()}

        correlation_id = request.headers.get("x-correlation-id") or f"corr-{uuid.uuid4()}"
        headers.setdefault("x-correlation-id", correlation_id)

        incoming_host = request.headers.get("host", "")
        forwarded_host = request.headers.get("x-forwarded-host", "")
        forwarded_proto = request.headers.get("x-forwarded-proto", "")
        forwarded_for = request.headers.get("x-forwarded-for", "")
        client_ip = (
            forwarded_for.split(",", 1)[0].strip() if forwarded_for else (request.client.host if request.client else "")
        )
        subscription_record = _find_subscription_by_id(cfg, auth.subscription.id) if auth.subscription else None
        subscription_owner = subscription_record.created_by if subscription_record is not None else None
        subscription_groups = (
            sorted(
                group.id for group in cfg.groups.values() if subscription_owner and subscription_owner in group.users
            )
            if subscription_owner
            else []
        )

        upstream_path = resolved.upstream_path
        upstream_query = dict(request.query_params)
        policy_req = PolicyRequest(
            method=request.method,
            path=upstream_path,
            query=upstream_query,
            headers=headers,
            variables={
                "route": route.name,
                "subscription_id": auth.subscription.id if auth.subscription else "",
                "products": auth.subscription_products,
                "client_ip": client_ip,
                "correlation_id": correlation_id,
                "incoming_host": incoming_host,
                "forwarded_host": forwarded_host,
                "forwarded_proto": forwarded_proto,
                "forwarded_for": forwarded_for,
                "subscription_owner": subscription_owner or "",
                "subscription_groups": subscription_groups,
                "rate_limit_store": request.app.state.rate_limit_store,
                "quota_store": request.app.state.quota_store,
                "original_request_url": str(request.url),
                "_request_headers": dict(headers),
                "_request_query": dict(upstream_query),
            },
            body=body,
        )

        trace_requested = cfg.trace_enabled and request.headers.get("x-apim-trace", "").lower() == "true"
        trace_id = f"trace-{int(time.time() * 1000)}" if trace_requested else None
        trace_collector = PolicyTraceCollector() if trace_requested else None
        client: httpx.AsyncClient = request.app.state.http_client
        policy_runtime = PolicyRuntime(
            gateway_config=cfg,
            http_client=client,
            timeout_seconds=cfg.proxy_timeout_seconds,
            trace=trace_collector,
            response_cache=request.app.state.policy_response_cache,
            value_cache=request.app.state.policy_value_cache,
        )

        def _store_trace(payload: dict[str, Any]) -> None:
            if not trace_id:
                return
            trace_store: dict[str, Any] = request.app.state.trace_store
            trace_store[trace_id] = {
                "trace_id": trace_id,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                **payload,
            }

        def _finalize_policy_response(
            *,
            status_code: int,
            headers: dict[str, str],
            body_bytes: bytes = b"",
            media_type: str | None = None,
        ) -> None:
            final_req = PolicyRequest(
                method=policy_req.method,
                path=policy_req.path,
                query=dict(policy_req.query),
                headers=dict(policy_req.headers),
                variables=policy_req.variables,
                body=policy_req.body,
                response_status_code=status_code,
                response_headers=headers,
                response_body=body_bytes,
                response_media_type=media_type,
            )
            finalize_deferred_actions(final_req, policy_runtime)

        trace_base = {
            "route": route.name,
            "correlation_id": correlation_id,
            "incoming_host": incoming_host,
            "forwarded_host": forwarded_host,
            "forwarded_proto": forwarded_proto,
            "forwarded_for": forwarded_for,
            "client_ip": client_ip,
            "upstream_url": None,
        }

        if policy_docs:
            early = await apply_inbound_async(policy_docs, policy_req, policy_runtime)
            if early is not None:
                out_headers = dict(early.headers)
                _finalize_policy_response(
                    status_code=early.status_code,
                    headers=out_headers,
                    body_bytes=early.body,
                    media_type=early.media_type,
                )
                out_headers["x-apim-simulator"] = "apim-sim-full"
                out_headers["x-correlation-id"] = correlation_id
                if trace_id:
                    out_headers["x-apim-trace-id"] = trace_id
                    trace = _trace_payload(
                        trace_base=trace_base,
                        trace_collector=trace_collector,
                        cfg=cfg,
                        extra={
                            "upstream_url": None,
                            "attempts": 0,
                            "status": early.status_code,
                            "elapsed_ms": 0,
                            "cache": None,
                            "reason": "policy_inbound_short_circuit",
                        },
                    )
                    out_headers["x-apim-trace"] = base64.b64encode(json.dumps(trace).encode("utf-8")).decode("utf-8")
                    _store_trace(trace)
                return Response(
                    content=early.body,
                    status_code=early.status_code,
                    headers=out_headers,
                    media_type=early.media_type,
                )

            backend_early = await apply_backend_async(policy_docs, policy_req, policy_runtime)
            if backend_early is not None:
                out_headers = dict(backend_early.headers)
                _finalize_policy_response(
                    status_code=backend_early.status_code,
                    headers=out_headers,
                    body_bytes=backend_early.body,
                    media_type=backend_early.media_type,
                )
                out_headers["x-apim-simulator"] = "apim-sim-full"
                out_headers["x-correlation-id"] = correlation_id
                if trace_id:
                    out_headers["x-apim-trace-id"] = trace_id
                    trace = _trace_payload(
                        trace_base=trace_base,
                        trace_collector=trace_collector,
                        cfg=cfg,
                        extra={
                            "upstream_url": None,
                            "attempts": 0,
                            "status": backend_early.status_code,
                            "elapsed_ms": 0,
                            "cache": None,
                            "reason": "policy_backend_short_circuit",
                        },
                    )
                    out_headers["x-apim-trace"] = base64.b64encode(json.dumps(trace).encode("utf-8")).decode("utf-8")
                    _store_trace(trace)
                return Response(
                    content=backend_early.body,
                    status_code=backend_early.status_code,
                    headers=out_headers,
                    media_type=backend_early.media_type,
                )

        effective_claims = auth.claims
        jwt_claims = policy_req.variables.get("_last_jwt_claims")
        if isinstance(jwt_claims, dict):
            effective_claims = jwt_claims
            _apply_claim_headers(policy_req.headers, effective_claims)

        if route.authz is not None:
            scopes = _extract_scopes(effective_claims)
            roles = _extract_roles(effective_claims)
            if route.authz.required_scopes and not set(route.authz.required_scopes).issubset(scopes):
                raise HTTPException(status_code=403, detail="Missing required scope")
            if route.authz.required_roles and not set(route.authz.required_roles).issubset(roles):
                raise HTTPException(status_code=403, detail="Missing required role")
            for key, expected in route.authz.required_claims.items():
                actual = effective_claims.get(key)
                if actual is None or str(actual) != expected:
                    raise HTTPException(status_code=403, detail="Missing required claim")

        upstream_base_url = route.upstream_base_url
        upstream_auth: tuple[str, str] | None = None
        selected_backend_url = str(policy_req.variables.get("selected_backend_url") or "")
        selected_backend_id = str(policy_req.variables.get("selected_backend_id") or "")
        backend_id = selected_backend_id or (route.backend or "" if not selected_backend_url else "")
        if selected_backend_url:
            upstream_base_url = selected_backend_url
        if backend_id:
            backend = cfg.backends.get(backend_id)
            if backend is not None:
                upstream_base_url = selected_backend_url or (
                    _render_backend_value(backend.url, policy_req, cfg) or backend.url
                )
                policy_req.headers.setdefault("x-apim-backend-id", backend_id)

                auth_type = (backend.auth_type or "none").lower()
                if auth_type == "basic":
                    username = _render_backend_value(backend.basic_username, policy_req, cfg)
                    password = _render_backend_value(backend.basic_password, policy_req, cfg)
                    if "authorization" not in policy_req.headers and username and password:
                        upstream_auth = (username, password)
                elif auth_type == "managed_identity":
                    policy_req.headers.setdefault("x-apim-managed-identity", "true")
                    if backend.managed_identity_resource:
                        policy_req.headers.setdefault(
                            "x-apim-managed-identity-resource",
                            _render_backend_value(backend.managed_identity_resource, policy_req, cfg),
                        )
                elif auth_type == "client_certificate":
                    policy_req.headers.setdefault("x-apim-client-certificate", "present")

                if (
                    backend.authorization_scheme
                    and backend.authorization_parameter
                    and "authorization" not in policy_req.headers
                ):
                    scheme = _render_backend_value(backend.authorization_scheme, policy_req, cfg) or ""
                    parameter = _render_backend_value(backend.authorization_parameter, policy_req, cfg) or ""
                    policy_req.headers["authorization"] = f"{scheme} {parameter}".strip()

                for header_name, header_value in backend.header_credentials.items():
                    rendered = _render_backend_value(header_value, policy_req, cfg)
                    if rendered is not None:
                        policy_req.headers[header_name.lower()] = rendered

                for query_name, query_value in backend.query_credentials.items():
                    rendered = _render_backend_value(query_value, policy_req, cfg)
                    if rendered is not None:
                        policy_req.query[query_name] = rendered

                if backend.client_certificate_thumbprints:
                    policy_req.headers.setdefault(
                        "x-apim-client-certificate-thumbprints",
                        ",".join(backend.client_certificate_thumbprints),
                    )

        if trace_collector is not None and trace_collector.selected_backend is None:
            trace_collector.selected_backend = {
                "backend_id": backend_id or None,
                "base_url": upstream_base_url,
            }

        upstream_url = route.build_upstream_url(policy_req.path, upstream_base_url=upstream_base_url)
        policy_req.variables["upstream_url"] = upstream_url

        trace_base["upstream_url"] = upstream_url

        policy_response_cache_active = bool(policy_req.variables.get("_policy_response_cache_active"))
        cache_key = None
        if (
            cfg.cache_enabled
            and (request.method == "GET")
            and (not cfg.proxy_streaming)
            and not policy_response_cache_active
        ):
            authz = request.headers.get("authorization", "")
            sub_key = request.headers.get("ocp-apim-subscription-key", "")
            material = (
                f"{request.method}|{upstream_url}|{json.dumps(policy_req.query, sort_keys=True)}|{authz}|{sub_key}"
            )
            cache_key = str(hash(material))
            cached = request.app.state.cache.get(cache_key)
            if cached is not None:
                expires_at, cached_status, cached_headers, cached_media_type, cached_body = cached
                if time.time() < expires_at:
                    out_headers = dict(cached_headers)
                    _finalize_policy_response(
                        status_code=cached_status,
                        headers=out_headers,
                        body_bytes=cached_body,
                        media_type=cached_media_type,
                    )
                    out_headers["x-apim-cache"] = "hit"
                    out_headers["x-correlation-id"] = correlation_id
                    if trace_id:
                        out_headers["x-apim-trace-id"] = trace_id
                        trace = _trace_payload(
                            trace_base=trace_base,
                            trace_collector=trace_collector,
                            cfg=cfg,
                            extra={
                                "attempts": 0,
                                "status": cached_status,
                                "elapsed_ms": 0,
                                "cache": "hit",
                            },
                        )
                        out_headers["x-apim-trace"] = base64.b64encode(json.dumps(trace).encode("utf-8")).decode(
                            "utf-8"
                        )
                        _store_trace(trace)
                    return Response(
                        content=cached_body,
                        status_code=cached_status,
                        headers=out_headers,
                        media_type=cached_media_type,
                    )
                request.app.state.cache.pop(cache_key, None)

        timeout = httpx.Timeout(cfg.proxy_timeout_seconds)
        max_attempts = max(1, cfg.proxy_max_attempts)
        last_exc: Exception | None = None
        upstream_response: httpx.Response | None = None
        start = time.perf_counter()
        attempts_used = 0

        for attempt in range(1, max_attempts + 1):
            attempts_used = attempt
            req = client.build_request(
                request.method,
                upstream_url,
                content=policy_req.body,
                headers=policy_req.headers,
                params=policy_req.query,
                timeout=timeout,
            )
            try:
                upstream_response = await client.send(req, stream=cfg.proxy_streaming, auth=upstream_auth)
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt >= max_attempts:
                    break
                continue

            if upstream_response.status_code in cfg.proxy_retry_statuses and attempt < max_attempts:
                await upstream_response.aclose()
                upstream_response = None
                continue
            break

        if upstream_response is None:
            if policy_docs:
                failure_req = PolicyRequest(
                    method=request.method,
                    path=policy_req.path,
                    query=dict(policy_req.query),
                    headers=dict(policy_req.headers),
                    variables={**policy_req.variables, "error": "upstream_unavailable"},
                )
                override = await apply_on_error_async(policy_docs, failure_req, policy_runtime)
                if override is not None:
                    out_headers = dict(override.headers)
                    _finalize_policy_response(
                        status_code=override.status_code,
                        headers=out_headers,
                        body_bytes=override.body,
                        media_type=override.media_type,
                    )
                    out_headers["x-apim-simulator"] = "apim-sim-full"
                    out_headers["x-correlation-id"] = correlation_id
                    if trace_id:
                        out_headers["x-apim-trace-id"] = trace_id
                        trace = _trace_payload(
                            trace_base=trace_base,
                            trace_collector=trace_collector,
                            cfg=cfg,
                            extra={
                                "attempts": attempts_used,
                                "status": override.status_code,
                                "elapsed_ms": int((time.perf_counter() - start) * 1000),
                                "cache": None,
                                "reason": "policy_on_error_override",
                            },
                        )
                        out_headers["x-apim-trace"] = base64.b64encode(json.dumps(trace).encode("utf-8")).decode(
                            "utf-8"
                        )
                        _store_trace(trace)
                    return Response(
                        content=override.body,
                        status_code=override.status_code,
                        headers=out_headers,
                        media_type=override.media_type,
                    )
            logger.exception("Unable to reach upstream", exc_info=last_exc)
            raise HTTPException(status_code=502, detail="Backend API unavailable")

        response_headers = filter_response_headers(dict(upstream_response.headers))
        media_type = upstream_response.headers.get("content-type")
        response_headers["x-correlation-id"] = correlation_id
        requires_buffering = cache_key is not None or policy_response_cache_active or not cfg.proxy_streaming
        content = b""
        if requires_buffering:
            content = await upstream_response.aread()
            await upstream_response.aclose()

        if policy_docs:
            outbound_req = PolicyRequest(
                method=request.method,
                path=policy_req.path,
                query=dict(policy_req.query),
                headers=response_headers,
                variables=policy_req.variables,
                body=policy_req.body,
                response_status_code=upstream_response.status_code,
                response_headers=response_headers,
                response_body=content,
                response_media_type=media_type,
            )
            await apply_outbound_async(policy_docs, outbound_req, policy_runtime)
            response_headers = outbound_req.headers
            content = outbound_req.response_body
            media_type = outbound_req.response_media_type or media_type

        _finalize_policy_response(
            status_code=upstream_response.status_code,
            headers=response_headers,
            body_bytes=content,
            media_type=media_type,
        )

        if cache_key is not None:
            response_headers["x-apim-cache"] = "miss"
            if len(request.app.state.cache) >= cfg.cache_max_entries:
                request.app.state.cache.clear()
            request.app.state.cache[cache_key] = (
                time.time() + cfg.cache_ttl_seconds,
                upstream_response.status_code,
                dict(response_headers),
                media_type,
                content,
            )
            if trace_requested:
                elapsed_ms = int((time.perf_counter() - start) * 1000)
                trace = _trace_payload(
                    trace_base=trace_base,
                    trace_collector=trace_collector,
                    cfg=cfg,
                    extra={
                        "attempts": attempts_used,
                        "status": upstream_response.status_code,
                        "elapsed_ms": elapsed_ms,
                        "cache": "miss",
                    },
                )
                response_headers["x-apim-trace-id"] = trace_id
                response_headers["x-apim-trace"] = base64.b64encode(json.dumps(trace).encode("utf-8")).decode("utf-8")
                _store_trace(trace)
            return Response(
                content=content,
                status_code=upstream_response.status_code,
                headers=response_headers,
                media_type=media_type,
            )

        if cfg.proxy_streaming and not requires_buffering:
            if trace_requested:
                elapsed_ms = int((time.perf_counter() - start) * 1000)
                trace = _trace_payload(
                    trace_base=trace_base,
                    trace_collector=trace_collector,
                    cfg=cfg,
                    extra={
                        "attempts": attempts_used,
                        "status": upstream_response.status_code,
                        "elapsed_ms": elapsed_ms,
                        "cache": None,
                    },
                )
                response_headers["x-apim-trace-id"] = trace_id
                response_headers["x-apim-trace"] = base64.b64encode(json.dumps(trace).encode("utf-8")).decode("utf-8")
                _store_trace(trace)
            return StreamingResponse(
                upstream_response.aiter_bytes(),
                status_code=upstream_response.status_code,
                headers=response_headers,
                media_type=media_type,
                background=BackgroundTask(upstream_response.aclose),
            )

        if trace_requested:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            trace = _trace_payload(
                trace_base=trace_base,
                trace_collector=trace_collector,
                cfg=cfg,
                extra={
                    "attempts": attempts_used,
                    "status": upstream_response.status_code,
                    "elapsed_ms": elapsed_ms,
                    "cache": None,
                },
            )
            response_headers["x-apim-trace-id"] = trace_id
            response_headers["x-apim-trace"] = base64.b64encode(json.dumps(trace).encode("utf-8")).decode("utf-8")
            _store_trace(trace)
        return Response(
            content=content,
            status_code=upstream_response.status_code,
            headers=response_headers,
            media_type=media_type,
        )

    return app


app = create_app()
