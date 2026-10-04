"""Guard version-selection semantics and avoid repeated full-table scans."""

import pytest
from starlette.requests import Request

import app.proxy as proxy
from app.config import ApiVersioningScheme, ApiVersionSetConfig, GatewayConfig, RouteConfig


def _route(name, version, prefix="/catalog", host=None):
    return RouteConfig(
        name=name,
        path_prefix=prefix,
        api_path_prefix=prefix,
        url_template="/resources/{id}",
        methods=["GET"],
        upstream_base_url="http://backend.test",
        api_version_set="catalog",
        api_version=version,
        host_match=[host] if host else [],
    )


def _config(scheme, routes):
    return GatewayConfig(
        routes=routes,
        api_version_sets={
            "catalog": ApiVersionSetConfig(
                display_name="Catalog",
                versioning_scheme=scheme,
                version_header_name="api-version",
                version_query_name="api-version",
            )
        },
    )


def _request(path="/catalog/resources/42", version="v2", **headers):
    values = {"host": "gateway.test", **headers}
    if version:
        values["api-version"] = version
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": f"api-version={version}".encode() if version else b"",
            "headers": [(key.encode(), value.encode()) for key, value in values.items()],
            "scheme": "http",
            "server": ("gateway.test", 80),
        }
    )


@pytest.mark.parametrize("scheme", list(ApiVersioningScheme))
@pytest.mark.parametrize("version", ["v2", "missing", None])
def test_version_selection_scans_once_per_prefix_including_no_match(monkeypatch, scheme, version):
    routes = [_route(f"{v}-{index}", v) for v in ("v1", "v2") for index in range(20)]
    cfg = _config(scheme, routes)
    calls = []
    original = proxy._resolve_versioned_route

    def tracked(*args, **kwargs):
        calls.append(kwargs["route"].name)
        return original(*args, **kwargs)

    monkeypatch.setattr(proxy, "_resolve_versioned_route", tracked)
    path = "/catalog/resources/42"
    if scheme == ApiVersioningScheme.Segment and version:
        path = f"/catalog/{version}/resources/42"
    result = proxy.resolve_route(cfg, _request(path, version))
    if version == "v2":
        assert result is not None and result.route.name == "v2-0"
        assert result.matched_parameters == {"id": "42"}
    else:
        assert result is None
    assert len(calls) == 1


def test_version_selection_cache_does_not_cross_forwarded_host_groups():
    cfg = _config(
        ApiVersioningScheme.Header,
        [_route("forwarded-v1", "v1", host="forwarded.test"), _route("direct-v2", "v2", host="gateway.test")],
    )
    result = proxy.resolve_route(cfg, _request(**{"x-forwarded-host": "forwarded.test"}))
    assert result is not None and result.route.name == "direct-v2"


def test_segment_version_selection_cache_keeps_distinct_api_prefixes():
    cfg = _config(
        ApiVersioningScheme.Segment,
        [_route("root", "v2"), _route("nested", "v2", prefix="/catalog/sub")],
    )
    result = proxy.resolve_route(cfg, _request("/catalog/sub/v2/resources/42"))
    assert result is not None and result.route.name == "nested"
    assert result.upstream_path == "/catalog/sub/resources/42"


def test_version_selection_cache_does_not_retain_previous_request_or_config_order():
    cfg = _config(ApiVersioningScheme.Header, [_route("first", "v2"), _route("second", "v2")])
    first = proxy.resolve_route(cfg, _request())
    assert first is not None and first.route.name == "first"
    cfg.routes.reverse()
    second = proxy.resolve_route(cfg, _request())
    assert second is not None and second.route.name == "second"
    assert proxy.resolve_route(cfg, _request(version="missing")) is None
