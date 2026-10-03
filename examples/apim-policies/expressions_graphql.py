"""Five policy-guide outcomes exercised against the published local gateway."""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from xml.sax.saxutils import escape

import httpx
import jwt

KEY = {"X-Apim-Tenant-Key": os.environ.get("APIM_TENANT_KEY", "local-dev-tenant-key")}
FIXTURES = Path(__file__).resolve().parents[2] / "tests/fixtures/policy_guides"
BACKEND = os.environ.get("POLICY_BACKEND_URL", "http://mock-backend:8080/api")


def manage(client, method, path, body=None):
    response = client.request(method, "/apim/management/" + path, headers=KEY, json=body)
    response.raise_for_status()
    return response.json()


def api(client, name, xml, method="GET"):
    manage(client, "PUT", "apis/" + name, {"name": name, "path": name, "upstream_base_url": BACKEND})
    manage(client, "PUT", f"apis/{name}/operations/call", {"name": "Call", "method": method, "url_template": "/echo"})
    manage(client, "PUT", "policies/api/" + name, {"xml": xml})


def policy(inbound="", outbound="", on_error=""):
    return f"<policies><inbound>{inbound}<base /></inbound><backend><base /></backend><outbound>{outbound}<base /></outbound><on-error>{on_error}<base /></on-error></policies>"


def backend_headers(response):
    response.raise_for_status()
    return httpx.Headers(response.json()["headers"])


def expressions(client):
    xml = policy(
        '<set-header name="math"><value>@((1+1).ToString())</value></set-header><set-header name="decoded"><value>'
        + escape(
            """@{ string[] value; if (context.Request.Headers.TryGetValue("Authorization", out value)) { if (value != null && value.Length > 0) { return Encoding.UTF8.GetString(Convert.FromBase64String(value[0])); } } return "missing"; }"""
        )
        + "</value></set-header>"
    )
    api(client, "guide-expressions", xml)
    response = client.get(
        "/guide-expressions/echo", headers={"Authorization": base64.b64encode(b"local-example").decode()}
    )
    response.raise_for_status()
    headers = backend_headers(response)
    assert headers["math"] == "2" and headers["decoded"] == "local-example"
    assert backend_headers(client.get("/guide-expressions/echo"))["decoded"] == "missing"
    return {"arithmetic": "2", "multi_statement": "local-example", "missing_header": "missing"}


def fragments(client):
    # The sample accesses a registered subscriber: create that local identity.
    manage(
        client,
        "PUT",
        "users/management",
        {"email": "guide@example.invalid", "first_name": "Guide", "last_name": "User"},
    )
    client.delete("/apim/management/subscriptions/guide-context", headers=KEY)
    manage(
        client,
        "POST",
        "subscriptions",
        {
            "name": "Guide context",
            "all_apis": True,
            "id": "guide-context",
            "primary_key": "guide-context-key",
            "secondary_key": "guide-context-secondary",
            "state": "active",
        },
    )
    sample = (
        (FIXTURES / "policy-fragments/example-01.xml")
        .read_text()
        .removeprefix("<fragment>")
        .strip()
        .removesuffix("</fragment>")
        .strip()
    )
    manage(client, "PUT", "policy-fragments/guide-context", {"xml": sample})
    for name in ["guide-fragment-a", "guide-fragment-b"]:
        api(client, name, policy('<include-fragment fragment-id="guide-context" />'))
        headers = backend_headers(
            client.get("/" + name + "/echo", headers={"Ocp-Apim-Subscription-Key": "guide-context-key"})
        )
        assert headers["x-request-context-data"].split(",")[0] == "management"
    manage(
        client,
        "PUT",
        "policy-fragments/guide-context",
        {"xml": '<set-header name="x-request-context-data"><value>updated</value></set-header>'},
    )
    for name in ["guide-fragment-a", "guide-fragment-b"]:
        assert backend_headers(client.get("/" + name + "/echo"))["x-request-context-data"] == "updated"
    deletion = client.delete("/apim/management/policy-fragments/guide-context", headers=KEY)
    assert deletion.status_code == 400
    return {"subscribed_user": "management", "shared_update": 2, "referenced_delete": 400}


def named_values(client, broker=None):
    values = {
        "ContosoHeader": {"value": "TrackingId"},
        "ContosoHeaderValue": {"value": "local-secret", "secret": True},
        "ExpressionProperty": {"value": "@(DateTime.Now.ToString())"},
        "ContosoHeaderValue2": {"value": "This is a header value."},
    }
    for name, body in values.items():
        manage(client, "PUT", "named-values/" + name, {"display_name": name, **body})
    samples = [(FIXTURES / f"api-management-howto-properties/example-{n:02}.xml").read_text() for n in [6, 8, 9]]
    samples[1] = samples[1].replace("CustomHeader", "ExpressionHeader")
    samples[2] = samples[2].replace("CustomHeader", "EncodedHeader")
    api(client, "guide-namedvalues", policy("".join(samples)))
    headers = backend_headers(client.get("/guide-namedvalues/echo"))
    assert headers["TrackingId"] == "local-secret"
    assert headers["EncodedHeader"] == "The URL encoded value is This+is+a+header+value."
    assert headers["ExpressionHeader"]
    assert "local-secret" not in str(manage(client, "GET", "named-values"))
    manage(client, "PUT", "named-values/ContosoHeaderValue", {"value": "rotated-local", "secret": True})
    assert backend_headers(client.get("/guide-namedvalues/echo"))["TrackingId"] == "rotated-local"
    manage(client, "PUT", "named-values/ContosoHeaderValue", {"display_name": "RenamedHeaderValue"})
    assert "{{RenamedHeaderValue}}" in str(manage(client, "GET", "policies/api/guide-namedvalues"))
    assert backend_headers(client.get("/guide-namedvalues/echo"))["TrackingId"] == "rotated-local"
    outcome = {
        "secret_masked": True,
        "value_rotated": True,
        "display_name_references_updated": True,
        "url_encoding": headers["EncodedHeader"],
    }
    if broker is not None:
        admin = {"Authorization": "Bearer " + os.environ.get("POLICY_BROKER_ADMIN_KEY", "local-broker-admin")}
        broker.put("/secrets/guide-secret", headers=admin, json={"value": "vault-before"}).raise_for_status()
        manage(
            client,
            "PUT",
            "named-values/VaultHeader",
            {"value_from_key_vault": {"secret_id": "https://vault.example.test/secrets/guide-secret"}},
        )
        api(client, "guide-vault", policy('<set-header name="VaultValue"><value>{{VaultHeader}}</value></set-header>'))
        assert backend_headers(client.get("/guide-vault/echo"))["VaultValue"] == "vault-before"
        broker.put("/secrets/guide-secret", headers=admin, json={"value": "vault-after"}).raise_for_status()
        assert backend_headers(client.get("/guide-vault/echo"))["VaultValue"] == "vault-after"
        assert "vault-after" not in str(manage(client, "GET", "named-values/VaultHeader"))
        outcome["local_vault_rotated_without_policy_reload"] = True
    return outcome


def errors(client):
    xml = (FIXTURES / "api-management-error-handling-policies/example-02.xml").read_text()
    xml = xml.replace(
        "<base />",
        '<check-header id="guide-required" name="required" failed-check-httpcode="403" failed-check-error-message="missing" ignore-case="false" /><base />',
        1,
    )
    api(client, "guide-errors", xml)
    response = client.get("/guide-errors/echo")
    assert response.status_code == 403
    assert response.headers["ErrorSource"] == "check-header"
    assert response.headers["ErrorPolicyId"] == "guide-required"
    assert response.headers["ErrorStatusCode"] == "403"
    return {"status": 403, "reason": response.headers["ErrorReason"], "policy_id": response.headers["ErrorPolicyId"]}


def graphql(client):
    api(client, "guide-graphql", policy(), method="POST")
    schema = "type Query { getComment(id: ID!): Comment, getBlog(id: ID!): Blog } type Comment { id: ID!, text: String } type Blog { id: ID!, title: String, comments: [Comment] }"
    manage(client, "PUT", "apis/guide-graphql/graphql", {"schema_document": schema})
    for name, type_name, field, xml in [
        (
            "comment",
            "Query",
            "getComment",
            (FIXTURES / "configure-graphql-resolver/example-06.xml")
            .read_text()
            .replace("https://data.contoso.com/api", BACKEND),
        ),
        (
            "comments",
            "Blog",
            "comments",
            (FIXTURES / "configure-graphql-resolver/example-03.xml")
            .read_text()
            .replace("https://data.contoso.com/api", BACKEND),
        ),
        (
            "blog",
            "Query",
            "getBlog",
            '<http-data-source><http-request><set-method>GET</set-method><set-url>@($"'
            + BACKEND
            + '/blog-record/{context.GraphQL.Arguments["id"]}")</set-url></http-request></http-data-source>',
        ),
    ]:
        manage(
            client,
            "PUT",
            "apis/guide-graphql/resolvers/" + name,
            {"name": name, "type_name": type_name, "field_name": field, "policies_xml": xml},
        )
    response = client.post(
        "/guide-graphql",
        json={
            "query": 'query($id: ID!){getComment(id:$id){id text} getBlog(id:"2"){title comments{text}}}',
            "variables": {"id": "7"},
        },
    )
    response.raise_for_status()
    expected = {
        "data": {
            "getComment": {"id": "7", "text": "Local comment"},
            "getBlog": {"title": "Local blog", "comments": [{"text": "Local comment"}]},
        }
    }
    assert response.json() == expected, response.text
    assert client.post("/guide-graphql", json={"query": "{missing}"}).status_code == 400
    return {"arguments": True, "variables": True, "parent": True, "schema_projection": True, "invalid_query": 400}


def role_filters(client):
    # The authoring guide provides a prompt rather than deterministic XML.
    # This local equivalent validates roles before choosing response fields.
    secret = os.environ.get("POLICY_GUIDE_SIGNING_KEY", "local-guide-signing-key-for-testing-only")
    encoded = base64.b64encode(secret.encode()).decode()
    inbound = (
        '<validate-jwt header-name="Authorization" require-scheme="Bearer" output-token-variable-name="role-token"><issuer-signing-keys><key>'
        + encoded
        + "</key></issuer-signing-keys><audiences><audience>local-guide</audience></audiences><issuers><issuer>https://issuer.example.test</issuer></issuers></validate-jwt>"
    )
    outbound = '<choose><when condition=\'@(context.Variables["role-token"].Claims["role"].Contains("admin"))\'><set-body>{"public":"visible","private":"admin-only"}</set-body></when><otherwise><set-body>{"public":"visible"}</set-body></otherwise></choose>'
    api(client, "guide-role-filter", policy(inbound, outbound))
    for role, expected in [
        ("member", {"public": "visible"}),
        ("admin", {"public": "visible", "private": "admin-only"}),
    ]:
        token = jwt.encode(
            {"iss": "https://issuer.example.test", "aud": "local-guide", "exp": int(time.time()) + 300, "role": role},
            secret,
            algorithm="HS256",
        )
        response = client.get("/guide-role-filter/echo", headers={"Authorization": "Bearer " + token})
        assert response.status_code == 200 and response.json() == expected, response.text
    invalid = client.get("/guide-role-filter/echo", headers={"Authorization": "Bearer invalid"})
    assert invalid.status_code == 401
    return {
        "validated_member_fields": ["public"],
        "validated_admin_fields": ["public", "private"],
        "invalid_token": 401,
    }


def run(client: httpx.Client, broker: httpx.Client | None = None) -> dict:
    expression_outcomes = expressions(client)
    expression_outcomes["role_filter_authoring"] = role_filters(client)
    return {
        "expressions": expression_outcomes,
        "fragments": fragments(client),
        "named_values": named_values(client, broker),
        "errors": errors(client),
        "graphql": graphql(client),
    }


if __name__ == "__main__":
    import json

    with httpx.Client(base_url=os.environ.get("APIM_BASE_URL", "http://localhost:8080"), timeout=30) as client:
        print(json.dumps(run(client), indent=2))
