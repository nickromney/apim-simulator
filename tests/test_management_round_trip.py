"""Declarative-client contract for the management API.

A declarative client (Terraform, OpenTofu, a GitOps reconciler) keeps the body
it last sent and compares it with what the server returns. That only works when
every writable resource:

- is one URL, addressed by an identifier the caller chooses;
- accepts PUT as a repeatable write that clears a field sent as null (a full
  replace, where omitting a field clears it too, is better still);
- returns from GET exactly what PUT accepted, apart from declared write-only
  secrets, which come back masked;
- rejects fields it does not store, so the client never records a value the
  server silently dropped;
- disappears after DELETE.

Each resource is declared once in RESOURCES. Known gaps are listed in
KNOWN_GAPS and run as strict xfails: fixing a gap makes its test pass, and the
strict marker then fails until the entry is removed.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import GatewayConfig, TenantAccessConfig
from app.main import create_app

TENANT_HEADERS = {"X-Apim-Tenant-Key": "tenant-test"}
MANAGEMENT = "/apim/management"
MASK = "***"

POLICY_XML = (
    "<policies><inbound><base /></inbound><backend><base /></backend>"
    "<outbound><base /></outbound><on-error><base /></on-error></policies>"
)
FRAGMENT_XML = (
    '<fragment><set-header name="x-fragment" exists-action="override"><value>1</value></set-header></fragment>'
)


@dataclass(frozen=True)
class Resource:
    """One writable management resource, as a declarative client sees it."""

    name: str
    path: str
    body: dict[str, Any]
    # Optional field set in `body` whose omission on a later PUT must clear it.
    clearable: str | None = None
    # Fields the server accepts but never returns in plain text.
    write_only: frozenset[str] = frozenset()
    # Resources that must exist first, as (path, body) PUTs.
    requires: tuple[tuple[str, dict[str, Any]], ...] = ()
    # Fields a GET returns once the resource is deleted, for resources that
    # always read as something (a scope with no policy reads as an empty
    # document). None means the GET must be a 404.
    reads_after_delete: dict[str, Any] | None = None

    @property
    def url(self) -> str:
        return f"{MANAGEMENT}/{self.path}"


API = ("apis/a1", {"name": "A1", "path": "a1", "upstream_base_url": "http://backend.example"})
PRODUCT = ("products/p1", {"name": "P1"})
LOGGER = ("loggers/log1", {"logger_type": "custom", "description": "local"})

RESOURCES: tuple[Resource, ...] = (
    Resource(
        "product",
        "products/p1",
        {
            "name": "P1",
            "description": "Starter",
            "state": "published",
            "require_subscription": True,
            "approval_required": False,
            "subscriptions_limit": 2,
            "terms": "Be nice",
        },
        clearable="description",
    ),
    Resource(
        "api",
        "apis/a1",
        {
            "name": "A1",
            "path": "a1",
            "upstream_base_url": "http://backend.example",
            "protocols": ["https"],
            "api_version": "v1",
            "gateways": ["managed"],
        },
        clearable="api_version",
    ),
    Resource(
        "api_operation",
        "apis/a1/operations/o1",
        {"name": "O1", "method": "POST", "url_template": "/items/{id}", "description": "Create"},
        clearable="description",
        requires=(API,),
    ),
    Resource(
        "api_policy",
        "policies/api/a1",
        {"xml": POLICY_XML},
        requires=(API,),
        reads_after_delete={"xml": "<policies><inbound /><backend /><outbound /><on-error /></policies>"},
    ),
    Resource(
        "backend",
        "backends/b1",
        {"url": "http://backend.example", "description": "Primary", "type": "single", "auth_type": "none"},
        clearable="description",
    ),
    Resource(
        "backend_with_secret",
        "backends/b2",
        {"url": "http://backend.example", "auth_type": "basic", "basic_username": "svc", "basic_password": "pw"},
        write_only=frozenset({"basic_password"}),
    ),
    Resource(
        "named_value",
        "named-values/nv1",
        {"display_name": "nv1", "value": "plain", "secret": False},
    ),
    Resource(
        "secret_named_value",
        "named-values/nv2",
        {"display_name": "nv2", "value": "hidden", "secret": True},
        write_only=frozenset({"value"}),
    ),
    Resource("tag", "tags/t1", {"display_name": "T1"}),
    Resource(
        "subscription",
        "subscriptions/s1",
        {"name": "S1", "state": "active", "products": ["p1"]},
        requires=(PRODUCT,),
    ),
    Resource("product_api_link", "products/p1/apis/a1", {}, requires=(PRODUCT, API)),
    Resource(
        "user",
        "users/u1",
        {"email": "u1@example.test", "first_name": "U", "last_name": "One", "note": "Tester", "state": "active"},
        clearable="note",
    ),
    Resource(
        "group",
        "groups/g1",
        {"name": "G1", "description": "Testers", "type": "custom"},
        clearable="description",
    ),
    Resource(
        "api_version_set",
        "api-version-sets/v1",
        {
            "display_name": "V1",
            "description": "Header versions",
            "versioning_scheme": "Header",
            "version_header_name": "api-version",
        },
        clearable="description",
    ),
    Resource("policy_fragment", "policy-fragments/f1", {"xml": FRAGMENT_XML}),
    Resource(
        "workspace",
        "workspaces/w1",
        {"display_name": "W1", "description": "Team"},
        clearable="description",
    ),
    Resource(
        "workspace_gateway",
        "workspace-gateways/wg1",
        {"display_name": "WG1", "description": "Edge", "host_names": ["wg1.example.test"]},
        clearable="description",
    ),
    Resource(
        "global_schema",
        "schemas/s1",
        {"type": "json", "value": '{"type":"object"}', "description": "Object"},
        clearable="description",
    ),
    Resource("logger", *LOGGER, clearable="description"),
    Resource(
        "diagnostic",
        "diagnostics/applicationinsights",
        {"logger_id": "log1", "sampling_percentage": 50.0, "verbosity": "information"},
        clearable="verbosity",
        requires=(LOGGER,),
    ),
)

KEEPS_OMITTED = "by design, a PUT keeps fields it leaves out; an explicit null clears them"

KNOWN_GAPS: dict[tuple[str, str], str] = {
    ("replace", "product"): KEEPS_OMITTED,
    ("replace", "api_operation"): KEEPS_OMITTED,
    ("replace", "workspace"): KEEPS_OMITTED,
}


def _params(check: str, resources: tuple[Resource, ...] = RESOURCES) -> list[Any]:
    params = []
    for resource in resources:
        reason = KNOWN_GAPS.get((check, resource.name))
        marks = [pytest.mark.xfail(strict=True, reason=reason)] if reason else []
        params.append(pytest.param(resource, id=resource.name, marks=marks))
    return params


@pytest.fixture
def client() -> Iterator[TestClient]:
    config = GatewayConfig(tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant-test"))
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: httpx.Response(200)))
    with TestClient(create_app(config=config, http_client=upstream)) as test_client:
        yield test_client


def _put(client: TestClient, url: str, body: dict[str, Any]) -> httpx.Response:
    return client.put(url, json=body, headers=TENANT_HEADERS)


def _create(client: TestClient, resource: Resource, body: dict[str, Any] | None = None) -> None:
    for path, dependency in resource.requires:
        response = _put(client, f"{MANAGEMENT}/{path}", dependency)
        assert response.status_code == 200, f"setup {path}: {response.text}"
    response = _put(client, resource.url, resource.body if body is None else body)
    assert response.status_code == 200, response.text


def _read(client: TestClient, resource: Resource) -> dict[str, Any]:
    response = client.get(resource.url, headers=TENANT_HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def _drift(sent: dict[str, Any], read: dict[str, Any], write_only: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Fields a declarative client would report as changed outside its control."""
    return {
        key: (value, read.get(key)) for key, value in sent.items() if key not in write_only and read.get(key) != value
    }


@pytest.mark.parametrize("resource", _params("round_trip"))
def test_get_returns_what_put_accepted(client: TestClient, resource: Resource) -> None:
    _create(client, resource)

    read = _read(client, resource)

    assert _drift(resource.body, read, resource.write_only) == {}
    for key in resource.write_only:
        assert read.get(key) == MASK, f"{key} must come back masked"


@pytest.mark.parametrize("resource", _params("idempotent"))
def test_repeating_put_changes_nothing(client: TestClient, resource: Resource) -> None:
    _create(client, resource)
    first = _read(client, resource)

    _create(client, resource)

    assert _read(client, resource) == first


@pytest.mark.parametrize("resource", _params("get_put"))
def test_put_of_what_get_returned_changes_nothing(client: TestClient, resource: Resource) -> None:
    """GET, edit, PUT: read-only fields and masked secrets sent back are harmless."""
    _create(client, resource)
    first = _read(client, resource)

    response = _put(client, resource.url, first)

    assert response.status_code == 200, response.text
    assert _read(client, resource) == first


def test_put_of_a_masked_secret_keeps_the_secret(client: TestClient) -> None:
    """The masked GET body cannot reveal a lost secret, so check the stored config."""
    for resource in RESOURCES:
        if resource.write_only:
            _create(client, resource)
            _put(client, resource.url, _read(client, resource))

    config = client.app.state.gateway_config
    assert config.backends["b2"].basic_password == "pw"
    assert config.named_values["nv2"].value == "hidden"


@pytest.mark.parametrize("resource", _params("replace", tuple(r for r in RESOURCES if r.clearable)))
def test_put_without_a_field_clears_it(client: TestClient, resource: Resource) -> None:
    assert resource.clearable is not None
    _create(client, resource)

    _create(client, resource, {k: v for k, v in resource.body.items() if k != resource.clearable})

    assert _read(client, resource).get(resource.clearable) is None


@pytest.mark.parametrize("resource", _params("null", tuple(r for r in RESOURCES if r.clearable)))
def test_put_with_an_explicit_null_clears_the_field(client: TestClient, resource: Resource) -> None:
    """The weaker guarantee a client can fall back on where PUT merges."""
    assert resource.clearable is not None
    _create(client, resource)

    _create(client, resource, {**resource.body, resource.clearable: None})

    assert _read(client, resource).get(resource.clearable) is None


@pytest.mark.parametrize("resource", _params("strict"))
def test_put_rejects_fields_it_would_not_store(client: TestClient, resource: Resource) -> None:
    for path, dependency in resource.requires:
        _put(client, f"{MANAGEMENT}/{path}", dependency)

    response = _put(client, resource.url, {**resource.body, "unexpected_field": "dropped"})

    assert response.status_code == 422


@pytest.mark.parametrize("resource", _params("delete"))
def test_delete_removes_the_resource(client: TestClient, resource: Resource) -> None:
    _create(client, resource)

    deleted = client.delete(resource.url, headers=TENANT_HEADERS)

    assert deleted.status_code in {200, 204}, deleted.text
    after = client.get(resource.url, headers=TENANT_HEADERS)
    if resource.reads_after_delete is None:
        assert after.status_code == 404
    else:
        assert after.status_code == 200, after.text
        assert _drift(resource.reads_after_delete, after.json()) == {}


def test_product_api_link_is_readable(client: TestClient) -> None:
    _put(client, f"{MANAGEMENT}/products/p1", {"name": "P1"})
    _put(client, f"{MANAGEMENT}/{API[0]}", API[1])
    assert _put(client, f"{MANAGEMENT}/products/p1/apis/a1", {}).status_code == 200

    assert client.get(f"{MANAGEMENT}/products/p1/apis/a1", headers=TENANT_HEADERS).status_code == 200


def test_subscription_is_created_by_put_to_its_id(client: TestClient) -> None:
    _put(client, f"{MANAGEMENT}/products/p1", {"name": "P1"})

    response = _put(client, f"{MANAGEMENT}/subscriptions/s1", {"name": "S1", "products": ["p1"]})

    assert response.status_code == 200


def test_updating_an_api_keeps_its_policy(client: TestClient) -> None:
    custom = POLICY_XML.replace("<inbound><base /></inbound>", "<inbound><base /><mock-response /></inbound>")
    _put(client, f"{MANAGEMENT}/{API[0]}", API[1])
    _put(client, f"{MANAGEMENT}/policies/api/a1", {"xml": custom})

    _put(client, f"{MANAGEMENT}/{API[0]}", {**API[1], "name": "Renamed"})

    assert client.get(f"{MANAGEMENT}/policies/api/a1", headers=TENANT_HEADERS).json()["xml"] == custom


def test_updating_an_operation_keeps_its_policy_and_tags(client: TestClient) -> None:
    custom = POLICY_XML.replace("<inbound><base /></inbound>", "<inbound><base /><mock-response /></inbound>")
    _put(client, f"{MANAGEMENT}/{API[0]}", API[1])
    _put(client, f"{MANAGEMENT}/tags/t1", {"display_name": "T1"})
    _put(client, f"{MANAGEMENT}/apis/a1/operations/o1", {"url_template": "/x"})
    _put(client, f"{MANAGEMENT}/policies/operation/a1:o1", {"xml": custom})
    assert _put(client, f"{MANAGEMENT}/apis/a1/operations/o1/tags/t1", {}).status_code == 200

    _put(client, f"{MANAGEMENT}/apis/a1/operations/o1", {"url_template": "/x", "name": "Renamed"})

    assert client.get(f"{MANAGEMENT}/policies/operation/a1:o1", headers=TENANT_HEADERS).json()["xml"] == custom
    assert client.get(f"{MANAGEMENT}/apis/a1/operations/o1", headers=TENANT_HEADERS).json()["tags"] == ["t1"]


def test_updating_an_api_keeps_its_operations(client: TestClient) -> None:
    _put(client, f"{MANAGEMENT}/{API[0]}", API[1])
    _put(client, f"{MANAGEMENT}/apis/a1/operations/o1", {"url_template": "/x"})

    _put(client, f"{MANAGEMENT}/{API[0]}", {**API[1], "name": "Renamed"})

    assert client.get(f"{MANAGEMENT}/apis/a1/operations/o1", headers=TENANT_HEADERS).status_code == 200


def test_every_resource_masks_secrets_the_same_way(client: TestClient) -> None:
    body = {"url": "http://backend.example", "auth_type": "basic", "basic_username": "u", "basic_password": "pw"}
    _put(client, f"{MANAGEMENT}/backends/b1", body)

    assert client.get(f"{MANAGEMENT}/backends/b1", headers=TENANT_HEADERS).json()["basic_password"] == MASK
