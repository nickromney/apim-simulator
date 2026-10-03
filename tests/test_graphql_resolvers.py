from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.config import ApiConfig, GatewayConfig, TenantAccessConfig
from app.graphql_resolvers import GraphQLApiConfig, GraphQLResolverConfig
from app.main import create_app

FIXTURES = Path("tests/fixtures/policy_guides/configure-graphql-resolver")
SCHEMA = """type Query { getComment(id: ID!): Comment, getBlog(id: ID!): Blog, ping: String }
type Comment { id: ID!, text: String }
type Blog { id: ID!, title: String, comments: [Comment] }"""
KEY = {"X-Apim-Tenant-Key": "operator"}


def _resolver(type_name, field, fixture=None, url="http://data.local/ping", extra=""):
    xml = (
        (FIXTURES / fixture).read_text()
        if fixture
        else f"<http-data-source><http-request><set-method>GET</set-method><set-url>{url}</set-url>{extra}</http-request></http-data-source>"
    )
    return GraphQLResolverConfig(name=field, type_name=type_name, field_name=field, policies_xml=xml)


def _client(resolvers=None):
    calls = []

    def upstream(req):
        calls.append(req)
        if req.url.path == "/api/comment/7":
            return httpx.Response(200, json={"id": "7", "text": "comment", "private": "omitted"})
        if req.url.path == "/api/blog/2":
            return httpx.Response(200, json=[{"id": "7", "text": "comment"}])
        if req.url.path == "/blog/2":
            return httpx.Response(200, json={"id": "2", "title": "blog"})
        return httpx.Response(200, text="pong")

    cfg = GatewayConfig(
        allow_anonymous=True,
        tenant_access=TenantAccessConfig(enabled=True, primary_key="operator"),
        apis={
            "gql": ApiConfig(
                name="GraphQL",
                path="graphql",
                upstream_base_url="http://unused",
                graphql=GraphQLApiConfig(schema_document=SCHEMA, resolvers=resolvers or {}),
                policies_xml='<policies><inbound><set-header name="api-only"><value>once</value></set-header><base /></inbound><backend><base /></backend><outbound><set-header name="api-out"><value>once</value></set-header><base /></outbound></policies>',
            )
        },
    )
    from app.config import OperationConfig

    cfg.apis["gql"].operations = {"graphql": OperationConfig(name="GraphQL", method="POST", url_template="/")}
    return TestClient(
        create_app(config=cfg, http_client=httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    ), calls


def test_documented_arguments_variables_parent_and_schema_projection():
    resolvers = {
        "comment": _resolver("Query", "getComment", "example-06.xml"),
        "blog": _resolver("Query", "getBlog", url='@($"http://data.local/blog/{context.GraphQL.Arguments["id"]}")'),
        "comments": _resolver("Blog", "comments", "example-03.xml"),
    }
    client, calls = _client(resolvers)
    with client:
        response = client.post(
            "/graphql",
            json={
                "query": 'query($id: ID!){ getComment(id:$id){id text} getBlog(id:"2"){title comments{text}} }',
                "variables": {"id": "7"},
            },
        )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "data": {
            "getComment": {"id": "7", "text": "comment"},
            "getBlog": {"title": "blog", "comments": [{"text": "comment"}]},
        }
    }
    assert len(calls) == 3
    assert all(req.url.host != "unused" for req in calls)
    assert response.headers["api-out"] == "once"


def test_literal_arguments_raw_string_and_invalid_query():
    client, calls = _client(
        {"comment": _resolver("Query", "getComment", "example-06.xml"), "ping": _resolver("Query", "ping")}
    )
    with client:
        assert client.post("/graphql", json={"query": '{getComment(id:"7"){text} ping}'}).json() == {
            "data": {"getComment": {"text": "comment"}, "ping": "pong"}
        }
        invalid = client.post("/graphql", json={"query": "{missing}"})
        assert invalid.status_code == 400
        assert "Cannot query field" in invalid.json()["errors"][0]["message"]
        assert client.post("/graphql", json={"query": "{}", "variables": []}).status_code == 400
    assert len(calls) == 2


def test_independent_resolver_request_and_response_policy():
    request = '<set-body>@(context.Request.Body.As&lt;string&gt;(preserveContent: true))</set-body><set-header name="field-only"><value>field</value></set-header>'
    resolver = _resolver("Query", "getComment", url="http://data.local/api/comment/7", extra=request)
    resolver.policies_xml = resolver.policies_xml.replace("<set-method>GET", "<set-method>POST").replace(
        "</http-data-source>",
        '<http-response><set-body>{"id":"8","text":"transformed"}</set-body></http-response></http-data-source>',
    )
    client, calls = _client({"comment": resolver, "ping": _resolver("Query", "ping")})
    with client:
        response = client.post("/graphql", json={"query": '{getComment(id:"7"){id text} ping}'})
    assert response.json() == {"data": {"getComment": {"id": "8", "text": "transformed"}, "ping": "pong"}}
    field = next(req for req in calls if req.url.path.endswith("/7"))
    assert json.loads(field.content) == {"id": "7"}
    assert field.headers["field-only"] == "field"
    assert field.headers["content-length"] == str(len(field.content))
    assert "content-length" not in next(req for req in calls if req.url.path == "/ping").headers
    assert "field-only" not in next(req for req in calls if req.url.path == "/ping").headers


def test_management_schema_resolver_update_clone_unlinked_delete_and_auth():
    client, calls = _client()
    body = _resolver("Query", "ping").model_dump()
    with client:
        assert client.put("/apim/management/apis/gql/graphql", json={"schema_document": SCHEMA}).status_code == 403
        assert (
            client.put(
                "/apim/management/apis/gql/graphql", headers=KEY, json={"schema_document": "invalid"}
            ).status_code
            == 400
        )
        assert (
            client.put("/apim/management/apis/gql/graphql", headers=KEY, json={"schema_document": SCHEMA}).status_code
            == 200
        )
        assert client.put("/apim/management/apis/gql/resolvers/ping", headers=KEY, json=body).status_code == 200
        clone = {**body, "name": "unlinked", "field_name": "missing"}
        assert client.put("/apim/management/apis/gql/resolvers/clone", headers=KEY, json=clone).status_code == 200
        listed = client.get("/apim/management/apis/gql/resolvers", headers=KEY).json()
        assert {row["id"]: row["linked"] for row in listed} == {"ping": True, "clone": False}
        assert client.post("/graphql", json={"query": "{ping}"}).json() == {"data": {"ping": "pong"}}
        assert client.delete("/apim/management/apis/gql/resolvers/ping", headers=KEY).status_code == 200
        assert client.post("/graphql", json={"query": "{ping}"}).json() == {"data": {"ping": None}}
    assert len(calls) == 1


def test_resolver_query_context_and_mutation_do_not_use_original_gateway_query():
    extra = '<set-header name="resolver-query"><value>@(context.Request.Url.Query["q"].Last())</value></set-header><set-query-parameter name="added"><value>yes</value></set-query-parameter>'
    client, calls = _client(
        {"ping": _resolver("Query", "ping", url="http://data.local/ping?q=one&amp;q=two", extra=extra)}
    )
    with client:
        assert client.post("/graphql?original=unused", json={"query": "{ping}"}).json() == {"data": {"ping": "pong"}}
    assert calls[0].headers["resolver-query"] == "two"
    assert calls[0].url.params.get_list("q") == ["one", "two"]
    assert calls[0].url.params["added"] == "yes"
    assert "original" not in calls[0].url.params
