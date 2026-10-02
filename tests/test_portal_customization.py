from __future__ import annotations

import base64

import pytest
import yaml
from test_portal import _client, _portal_config

from app.config import (
    ApiConfig,
    ApiVersionSetConfig,
    GatewayConfig,
    GroupConfig,
    OperationConfig,
    PortalConfig,
    TenantAccessConfig,
)
from app.portal_customization import PortalContentConfig

TENANT = {"X-Apim-Tenant-Key": "t1"}
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNIM575HwAEZwIymUV1gwAAAABJRU5ErkJggg=="


def test_draft_preview_and_publication_are_separate_snapshots() -> None:
    with _client(_portal_config()) as client:
        original = client.get("/apim/portal").text
        site = client.get("/apim/portal/editor/draft", headers=TENANT).json()["site"]
        site.update(site_title="Contoso developer center", accent_color="#442288", theme="dark")
        site["pages"][0].update(title="Welcome Contoso developers", content="Build an integration with our APIs.")
        site["pages"].append({"slug": "getting-started", "title": "Getting started", "content": "Request a key."})
        saved = client.put("/apim/portal/editor/draft", headers=TENANT, json=site)
        assert saved.status_code == 200
        assert client.get("/apim/portal").text == original
        assert client.get("/apim/portal/content").json()["publication"] == 0
        assert client.get("/apim/portal/pages/getting-started").status_code == 404
        preview = client.get("/apim/portal/editor/preview", headers=TENANT)
        assert "Welcome Contoso developers" in preview.text
        assert "--accent:#442288" in preview.text
        assert "color-scheme:dark" in preview.text
        assert preview.headers["cache-control"] == "no-store"
        assert client.post("/apim/portal/editor/publish", headers=TENANT).json()["publication"] == 1
        public = client.get("/apim/portal").text
        assert "Contoso developer center" in public and "Welcome Contoso developers" in public
        assert "Product catalog" in public and "try-send" in public
        page = client.get("/apim/portal/pages/getting-started")
        assert page.status_code == 200 and "Request a key." in page.text
        assert "--accent:#442288" in page.text
        site["pages"][0]["content"] = "Unpublished new draft text"
        client.put("/apim/portal/editor/draft", headers=TENANT, json=site).raise_for_status()
        assert "Unpublished new draft text" not in client.get("/apim/portal").text
        assert "Unpublished new draft text" not in str(client.get("/apim/portal/content").json())


@pytest.mark.parametrize("headers", [{}, {"X-Apim-Tenant-Key": "invalid"}, {"X-Apim-Portal-User": "dev-1"}])
def test_drafts_cannot_be_read_or_written_by_consumers(headers) -> None:
    with _client(_portal_config()) as client:
        assert client.get("/apim/portal/editor/draft", headers=headers).status_code == 403
        assert client.get("/apim/portal/editor/preview", headers=headers).status_code == 403
        assert (
            client.put("/apim/portal/editor/draft", headers=headers, json={"site_title": "Unauthorized"}).status_code
            == 403
        )
        assert client.post("/apim/portal/editor/publish", headers=headers).status_code == 403
        assert client.get("/apim/portal/content").json()["publication"] == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"logo_url": "javascript:alert(1)"},
        {"logo_url": "//example.com/logo.svg"},
        {"background_image_url": "data:image/svg+xml,<svg onload=alert(1)>"},
        {"background_image_url": "/\\evil.com/logo.png"},
        {"accent_color": "red;display:none"},
        {"theme": "other"},
        {"pages": []},
        {"pages": [{"slug": "home", "title": "Home"}, {"slug": "home", "title": "Duplicate"}]},
        {"pages": [{"slug": "../private", "title": "Private"}]},
    ],
)
def test_invalid_site_values_are_rejected(changes) -> None:
    with _client(_portal_config()) as client:
        site = client.get("/apim/portal/editor/draft", headers=TENANT).json()["site"]
        site.update(changes)
        assert client.put("/apim/portal/editor/draft", headers=TENANT, json=site).status_code == 422
        assert client.get("/apim/portal/content").json()["publication"] == 0


def test_custom_content_is_escaped_and_image_urls_do_not_break_styles() -> None:
    with _client(_portal_config()) as client:
        site = client.get("/apim/portal/editor/draft", headers=TENANT).json()["site"]
        site.update(
            site_title='<script>alert("title")</script>',
            logo_url='/logo.png"onerror="alert(1)',
            background_image_url="https://example.com/image'</style><script>alert(1)</script>.png",
        )
        site["pages"][0].update(title='<img src=x onerror="alert(1)">', content="<script>alert('body')</script>")
        assert client.put("/apim/portal/editor/draft", headers=TENANT, json=site).status_code == 200
        client.post("/apim/portal/editor/publish", headers=TENANT).raise_for_status()
        page = client.get("/apim/portal").text
        assert '<script>alert("title")</script>' not in page
        assert "<script>alert('body')</script>" not in page
        assert "&lt;script&gt;" in page
        assert "/logo.png&quot;onerror=&quot;alert(1)" in page
        assert "image%27%3C/style%3E%3Cscript%3E" in page


def test_portal_customization_survives_config_persistence(tmp_path, monkeypatch) -> None:
    path = tmp_path / "gateway.yaml"
    monkeypatch.setenv("APIM_CONFIG_PATH", str(path))
    with _client(_portal_config()) as client:
        site = client.get("/apim/portal/editor/draft", headers=TENANT).json()["site"]
        site["site_title"] = "Persisted portal"
        client.put("/apim/portal/editor/draft", headers=TENANT, json=site).raise_for_status()
        client.post("/apim/portal/editor/publish", headers=TENANT).raise_for_status()
        site["site_title"] = "Private draft after publication"
        client.put("/apim/portal/editor/draft", headers=TENANT, json=site).raise_for_status()
    reloaded = GatewayConfig.model_validate(yaml.safe_load(path.read_text()))
    assert reloaded.portal_content.draft.site_title == "Private draft after publication"
    assert reloaded.portal_content.published.site_title == "Persisted portal"
    with _client(reloaded) as client:
        assert "Persisted portal" in client.get("/apim/portal").text
        assert "Private draft after publication" not in client.get("/apim/portal").text


def test_disabled_portal_and_disabled_tenant_access_protect_editor() -> None:
    config = _portal_config(portal=PortalConfig(enabled=False))
    with _client(config) as client:
        for path in ["editor", "content", "editor/draft", "editor/preview", "pages/home"]:
            assert client.get("/apim/portal/" + path, headers=TENANT).status_code == 404
    config = _portal_config(tenant_access=TenantAccessConfig(enabled=False))
    with _client(config) as client:
        assert client.get("/apim/portal/editor/draft", headers=TENANT).status_code == 404


def test_default_snapshot_objects_are_independent() -> None:
    content = PortalContentConfig()
    content.draft.pages[0].title = "Draft"
    assert content.published.pages[0].title == "Developer Portal"


def test_portal_terms_must_be_accepted() -> None:
    config = _portal_config()
    config.products["starter"].terms = "Use these APIs responsibly."
    with _client(config) as client:
        headers = {"X-Apim-Portal-User": "dev-1"}
        body = {"product_id": "starter"}
        rejected = client.post("/apim/portal/subscriptions", json=body, headers=headers)
        assert rejected.status_code == 400 and "terms" in rejected.json()["detail"]
        assert client.get("/apim/portal/subscriptions", headers=headers).json()["subscriptions"] == []
        accepted = client.post("/apim/portal/subscriptions", json={**body, "accept_terms": True}, headers=headers)
        assert accepted.status_code == 201


def test_open_and_unpublished_products_are_visible_only_to_administrators() -> None:
    config = _portal_config()
    config.groups["administrators"] = GroupConfig(id="administrators", name="Administrators", users=["dev-2"])
    with _client(config) as client:
        regular = client.get("/apim/portal/catalog", headers={"X-Apim-Portal-User": "dev-1"}).json()
        admin = client.get("/apim/portal/catalog", headers={"X-Apim-Portal-User": "dev-2"}).json()
        assert "open" not in {product["id"] for product in regular["products"]}
        assert {"open", "internal"} <= {product["id"] for product in admin["products"]}


def test_image_upload_preview_and_publication_preserve_draft_privacy(tmp_path, monkeypatch) -> None:
    path = tmp_path / "portal.yaml"
    monkeypatch.setenv("APIM_CONFIG_PATH", str(path))
    body = {"name": "logo.png", "content_type": "image/png", "content_base64": PNG}
    with _client(_portal_config()) as client:
        assert client.post("/apim/portal/editor/media", json=body).status_code == 403
        uploaded = client.post("/apim/portal/editor/media", headers=TENANT, json=body)
        assert uploaded.status_code == 201
        url = uploaded.json()["url"]
        assert client.get(url).status_code == 404
        site = client.get("/apim/portal/editor/draft", headers=TENANT).json()["site"]
        site["logo_url"] = url
        client.put("/apim/portal/editor/draft", headers=TENANT, json=site).raise_for_status()
        assert "data:image/png;base64," + PNG in client.get("/apim/portal/editor/preview", headers=TENANT).text
        assert PNG not in client.get("/apim/portal").text
        assert client.get("/apim/portal/content").json()["site"]["media"] == []
        client.post("/apim/portal/editor/publish", headers=TENANT).raise_for_status()
        image = client.get(url)
        assert image.status_code == 200
        assert image.content == base64.b64decode(PNG)
        assert image.headers["content-type"] == "image/png"
        assert image.headers["x-content-type-options"] == "nosniff"
    config = GatewayConfig.model_validate(yaml.safe_load(path.read_text()))
    with _client(config) as client:
        assert client.get(url).content == base64.b64decode(PNG)


@pytest.mark.parametrize(
    "content_type,content",
    [
        ("image/svg+xml", base64.b64encode(b'<svg onload="alert(1)"></svg>').decode()),
        ("image/png", base64.b64encode(b"<script>alert(1)</script>").decode()),
        ("image/jpeg", PNG),
        ("image/png", base64.b64encode(base64.b64decode(PNG)[:-1] + b"\x00").decode()),
        ("image/webp", PNG),
        ("image/png", "not base64!"),
        ("image/png", base64.b64encode(b"\x89PNG" + b"a" * (2 * 1024 * 1024)).decode()),
    ],
)
def test_media_rejects_invalid_formats_and_oversized_files(content_type, content) -> None:
    with _client(_portal_config()) as client:
        response = client.post(
            "/apim/portal/editor/media",
            headers=TENANT,
            json={"name": "image", "content_type": content_type, "content_base64": content},
        )
        assert response.status_code == 422
        assert client.get("/apim/portal/editor/draft", headers=TENANT).json()["site"]["media"] == []


def test_media_library_has_a_bounded_size() -> None:
    with _client(_portal_config()) as client:
        body = {"name": "image.png", "content_type": "image/png", "content_base64": PNG}
        for _ in range(10):
            assert client.post("/apim/portal/editor/media", headers=TENANT, json=body).status_code == 201
        assert client.post("/apim/portal/editor/media", headers=TENANT, json=body).status_code == 409


def test_multiple_subscriptions_are_available_up_to_product_limit() -> None:
    config = _portal_config()
    config.products["starter"].subscriptions_limit = 2
    with _client(config) as client:
        headers = {"X-Apim-Portal-User": "dev-1"}
        body = {"product_id": "starter"}
        first = client.post("/apim/portal/subscriptions", headers=headers, json=body)
        second = client.post("/apim/portal/subscriptions", headers=headers, json=body)
        third = client.post("/apim/portal/subscriptions", headers=headers, json=body)
        assert first.status_code == second.status_code == 201
        assert first.json()["id"] == "dev-1-starter"
        assert second.json()["id"] == "dev-1-starter-2"
        assert first.json()["keys"]["primary"] != second.json()["keys"]["primary"]
        assert third.status_code == 409
        assert len(client.get("/apim/portal/subscriptions", headers=headers).json()["subscriptions"]) == 2


@pytest.mark.parametrize(
    "scheme,url,headers",
    [
        ("Segment", "/versioned/v1/greet", {}),
        ("Header", "/versioned/greet", {"x-api-version": "v1"}),
        ("Query", "/versioned/greet?api-version=v1", {}),
    ],
)
def test_portal_version_picker_projects_executable_original_and_versioned_requests(scheme, url, headers) -> None:
    version_set = ApiVersionSetConfig(
        display_name="Greeting API",
        versioning_scheme=scheme,
        version_header_name="x-api-version" if scheme == "Header" else None,
        version_query_name="api-version" if scheme == "Query" else None,
    )
    original = ApiConfig(
        name="Greeting original",
        path="versioned",
        upstream_base_url="http://upstream",
        api_version_set="greeting",
        products=["starter"],
        operations={"greet": OperationConfig(name="Greet", url_template="/greet")},
    )
    versioned = original.model_copy(deep=True)
    versioned.api_version = "v1"
    versioned.name = "Greeting v1"
    versioned.policies_xml = '<policies><outbound><set-header name="x-selected-version" exists-action="override"><value>v1</value></set-header></outbound></policies>'
    config = _portal_config(
        routes=[], apis={"original": original, "greeting-v1": versioned}, api_version_sets={"greeting": version_set}
    )
    with _client(config) as client:
        catalog = client.get("/apim/portal/catalog", headers={"X-Apim-Portal-User": "dev-1"}).json()
        starter = next(product for product in catalog["products"] if product["id"] == "starter")
        first, second = starter["apis"]
        assert first["api_version"] is None and second["api_version"] == "v1"
        assert first["api_version_set"] == second["api_version_set"] == "greeting"
        assert first["version_set_name"] == "Greeting API"
        assert first["versioning"]["scheme"] == scheme
        assert first["operations"][0]["request_url"] == "/versioned/greet"
        assert first["operations"][0]["request_headers"] == {}
        operation = second["operations"][0]
        assert operation["request_url"] == url and operation["request_headers"] == headers
        created = client.post(
            "/apim/portal/subscriptions", headers={"X-Apim-Portal-User": "dev-1"}, json={"product_id": "starter"}
        )
        key = created.json()["keys"]["primary"]
        response = client.get(
            operation["request_url"], headers={**operation["request_headers"], "Ocp-Apim-Subscription-Key": key}
        )
        assert response.status_code == 200 and response.headers["x-selected-version"] == "v1"
        original_response = client.get(
            first["operations"][0]["request_url"], headers={"Ocp-Apim-Subscription-Key": key}
        )
        assert original_response.status_code == 200 and "x-selected-version" not in original_response.headers
