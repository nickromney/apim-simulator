"""Local schema-driven GraphQL APIs and independent HTTP field resolvers."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit
from xml.etree import ElementTree as XmlTree

import httpx
from defusedxml import ElementTree
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field


class GraphQLResolverConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type_name: str
    field_name: str
    description: str | None = None
    policies_xml: str


class GraphQLApiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_document: str = Field(min_length=1, max_length=1048576)
    resolvers: dict[str, GraphQLResolverConfig] = Field(default_factory=dict)


def _resolver_root(xml: str) -> Any:
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ValueError("Invalid resolver policy XML") from exc
    if root.tag != "http-data-source":
        raise ValueError("Resolver policy must have an http-data-source root")
    request = root.find("http-request")
    if request is None or request.find("set-method") is None or request.find("set-url") is None:
        raise ValueError("HTTP resolver requires http-request, set-method, and set-url")
    if any(child.tag not in {"http-request", "backend", "http-response"} for child in root):
        raise ValueError("Invalid element in HTTP resolver")
    return root


def _resolver_policy(root: Any, section: str, cfg: Any) -> Any:
    from app.policy import parse_policies_xml

    source = root.find({"inbound": "http-request", "backend": "backend", "outbound": "http-response"}[section])
    policy = XmlTree.Element("policies")
    target = XmlTree.SubElement(policy, section)
    for child in list(source) if source is not None else []:
        if child.tag not in {"set-method", "set-url"}:
            target.append(deepcopy(child))
    return parse_policies_xml(
        XmlTree.tostring(policy, encoding="unicode"), gateway_config=cfg, policy_fragments=cfg.policy_fragments
    )


def validate_graphql_api(graphql_api: GraphQLApiConfig, cfg: Any) -> None:
    from graphql import build_schema, validate_schema

    try:
        schema = build_schema(graphql_api.schema_document)
        errors = validate_schema(schema)
        if errors:
            raise ValueError(errors[0].message)
        pairs = [(resolver.type_name, resolver.field_name) for resolver in graphql_api.resolvers.values()]
        if len(pairs) != len(set(pairs)):
            raise ValueError("Only one resolver may target a type and field")
        for resolver in graphql_api.resolvers.values():
            root = _resolver_root(resolver.policies_xml)
            _resolver_policy(root, "inbound", cfg)
            _resolver_policy(root, "backend", cfg)
            _resolver_policy(root, "outbound", cfg)
    except Exception as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError(str(exc)) from exc


def _resolver_url(req: Any, url: str) -> httpx.URL:
    backend = req.variables.get("selected_backend_url")
    result = httpx.URL(backend or url)
    path = result.path.rstrip("/") + "/" + req.path.lstrip("/") if backend else req.path
    return result.copy_with(path=path, query=urlencode(req.query.as_pairs()).encode())


async def _run_http_resolver(
    resolver: GraphQLResolverConfig, parent: Any, arguments: dict[str, Any], info: Any, original: Any, runtime: Any
) -> Any:
    from graphql import GraphQLScalarType, get_named_type

    from app.policy import (
        MultiValueMap,
        PolicyRequest,
        apply_backend_async,
        apply_inbound_async,
        apply_outbound_async,
        render_policy_value,
    )

    root = _resolver_root(resolver.policies_xml)
    request = root.find("http-request")
    variables = dict(original.variables)
    variables.update(_graphql_arguments=arguments, _graphql_parent=parent)
    variables.pop("selected_backend_url", None)
    variables.pop("selected_backend_id", None)
    request_headers = original.headers.copy()
    variables["_request_headers"] = request_headers
    req = PolicyRequest(
        method="GET",
        path="/",
        query={},
        headers=request_headers,
        variables=variables,
        body=json.dumps(arguments).encode(),
    )
    req.method = render_policy_value(request.find("set-method").text or "", req, runtime).upper()
    url = render_policy_value(request.find("set-url").text or "", req, runtime).strip()
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Resolver set-url must resolve to an HTTP(S) URL")
    req.path = parts.path
    req.query = MultiValueMap(parse_qs(parts.query, keep_blank_values=True))
    variables["_request_query"] = req.query
    variables["_request_path"] = req.path
    policy = _resolver_policy(root, "inbound", runtime.gateway_config)
    policy = replace(policy, scope=f"resolver:{resolver.type_name}/{resolver.field_name}")
    early = await apply_inbound_async([policy], req, runtime)
    if early is not None:
        raise ValueError("Resolver request policy ended before its HTTP data source")
    backend = _resolver_policy(root, "backend", runtime.gateway_config)
    await apply_backend_async([backend], req, runtime)
    response = await runtime.http_client.request(
        req.method,
        _resolver_url(req, url),
        headers=[(name, value) for name, value in req.headers.as_header_pairs() if name.lower() != "content-length"],
        content=req.body if req.method not in {"GET", "HEAD"} else None,
        timeout=req.variables.get("_forward_request_timeout_seconds", runtime.timeout_seconds),
    )
    response.raise_for_status()
    req.response_status_code = response.status_code
    req.response_body = response.content
    req.response_headers = MultiValueMap(dict(response.headers))
    req.headers = req.response_headers
    if root.find("http-response") is not None:
        policy = _resolver_policy(root, "outbound", runtime.gateway_config)
        policy = replace(policy, scope=f"resolver:{resolver.type_name}/{resolver.field_name}")
        override = await apply_outbound_async([policy], req, runtime)
        if override is not None:
            req.response_body = override.body
    text = req.response_body.decode("utf-8")
    # A String field can return the documented raw HTTP string. Object/list
    # fields consume the JSON text before schema projection and serialization.
    named_type = get_named_type(info.return_type)
    if isinstance(named_type, GraphQLScalarType) and named_type.name == "String":
        return text
    return json.loads(text)


async def execute_graphql_request(graphql_api: GraphQLApiConfig, req: Any, runtime: Any) -> httpx.Response:
    from graphql import build_schema, default_field_resolver, graphql

    try:
        payload = json.loads(req.body)
        if not isinstance(payload, dict) or not isinstance(payload.get("query"), str):
            raise ValueError("GraphQL request must include a query string")
        if payload.get("variables") is not None and not isinstance(payload["variables"], dict):
            raise ValueError("GraphQL variables must be an object")
    except (ValueError, UnicodeDecodeError) as exc:
        return httpx.Response(
            400, json={"errors": [{"message": str(exc)}]}, request=httpx.Request(req.method, "http://apim.local")
        )
    schema = build_schema(graphql_api.schema_document)
    resolvers = {(resolver.type_name, resolver.field_name): resolver for resolver in graphql_api.resolvers.values()}

    async def resolve(parent: Any, info: Any, **arguments: Any) -> Any:
        resolver = resolvers.get((info.parent_type.name, info.field_name))
        if resolver is None:
            return default_field_resolver(parent, info, **arguments)
        return await _run_http_resolver(resolver, parent, arguments, info, req, runtime)

    result = await graphql(
        schema,
        payload["query"],
        variable_values=payload.get("variables"),
        operation_name=payload.get("operationName"),
        field_resolver=resolve,
    )
    status = 400 if result.data is None and result.errors and not any(error.path for error in result.errors) else 200
    return httpx.Response(status, json=result.formatted, request=httpx.Request(req.method, "http://apim.local"))


def build_graphql_management_router(*, require_management_plane: Callable[[], Any]) -> APIRouter:  # noqa: C901 - route declarations; see docs/complexity.md
    from app.config import OperationConfig
    from app.security import require_tenant_access

    router = APIRouter()

    def api_config(request: Request, api_id: str) -> tuple[Any, Any]:
        require_tenant_access(request)
        cfg = request.app.state.gateway_config.model_copy(deep=True)
        api = cfg.apis.get(api_id)
        if api is None:
            raise HTTPException(status_code=404, detail="API not found")
        return cfg, api

    def persist(cfg: Any) -> None:
        try:
            require_management_plane().persist_or_apply_config(cfg)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.put("/apim/management/apis/{api_id}/graphql")
    async def import_schema(request: Request, api_id: str, body: GraphQLApiConfig) -> dict[str, Any]:
        cfg, api = api_config(request, api_id)
        api.graphql = body
        api.operations = {"graphql": OperationConfig(name="GraphQL", method="POST", url_template="/")}
        persist(cfg)
        return body.model_dump(mode="json")

    @router.get("/apim/management/apis/{api_id}/graphql")
    async def get_schema(request: Request, api_id: str) -> dict[str, Any]:
        _, api = api_config(request, api_id)
        if api.graphql is None:
            raise HTTPException(status_code=404, detail="GraphQL schema not found")
        return api.graphql.model_dump(mode="json")

    @router.get("/apim/management/apis/{api_id}/resolvers")
    async def list_resolvers(request: Request, api_id: str) -> list[dict[str, Any]]:
        _, api = api_config(request, api_id)
        if api.graphql is None:
            raise HTTPException(status_code=404, detail="GraphQL schema not found")
        from graphql import build_schema

        schema = build_schema(api.graphql.schema_document)
        return [
            {
                "id": id,
                **resolver.model_dump(mode="json"),
                "linked": resolver.field_name in getattr(schema.get_type(resolver.type_name), "fields", {}),
            }
            for id, resolver in api.graphql.resolvers.items()
        ]

    @router.put("/apim/management/apis/{api_id}/resolvers/{resolver_id}")
    async def put_resolver(
        request: Request, api_id: str, resolver_id: str, body: GraphQLResolverConfig
    ) -> dict[str, Any]:
        cfg, api = api_config(request, api_id)
        if api.graphql is None:
            raise HTTPException(status_code=400, detail="Import a GraphQL schema before creating resolvers")
        api.graphql.resolvers[resolver_id] = body
        persist(cfg)
        return {"id": resolver_id, **body.model_dump(mode="json")}

    @router.delete("/apim/management/apis/{api_id}/resolvers/{resolver_id}")
    async def delete_resolver(request: Request, api_id: str, resolver_id: str) -> dict[str, Any]:
        cfg, api = api_config(request, api_id)
        if api.graphql is None or resolver_id not in api.graphql.resolvers:
            raise HTTPException(status_code=404, detail="GraphQL resolver not found")
        del api.graphql.resolvers[resolver_id]
        persist(cfg)
        return {"deleted": True, "id": resolver_id}

    return router
