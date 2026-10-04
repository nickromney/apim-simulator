"""Cached path parsing must preserve mutable routing results and configuration."""

import pytest
from starlette.datastructures import QueryParams

from app.config import RouteConfig, _path_segments


@pytest.mark.parametrize(
    ("template", "path", "expected"),
    [
        ("/{id}", "/catalog/42/", {"id": "42"}),
        ("/{id}", "catalog/42", {"id": "42"}),
        ("/{*rest}", "///catalog/a//b///", {"rest": "a//b"}),
        ("/straße/{id}", "/CATALOG/STRASSE/%2F", {"id": "%2F"}),
        ("/{*rest}", "/catalog/../😀/\\", {"rest": "../😀/\\"}),
    ],
)
def test_cached_paths_keep_normalization_and_fresh_parameters(template, path, expected):
    route = RouteConfig(
        name="catalog",
        path_prefix="/catalog",
        api_path_prefix="/catalog",
        url_template=template,
        upstream_base_url="http://backend.test",
    )
    first = route.match(method="GET", path=path, query=QueryParams())
    assert first is not None and first.parameters == expected
    first.parameters["injected"] = "changed"
    second = route.match(method="GET", path=path, query=QueryParams())
    assert second is not None and second.parameters == expected


def test_cached_paths_observe_configuration_edits():
    route = RouteConfig(
        name="catalog",
        path_prefix="/catalog",
        api_path_prefix="/catalog",
        url_template="/{id}",
        upstream_base_url="http://backend.test",
    )
    assert route.match(method="GET", path="/catalog/42", query={}) is not None
    route.api_path_prefix = "/orders"
    route.path_prefix = "/orders"
    route.url_template = "/{number}/details"
    assert route.match(method="GET", path="/catalog/42", query={}) is None
    match = route.match(method="GET", path="/orders/73/details", query={})
    assert match is not None and match.parameters == {"number": "73"}
    assert not route.matches_api_path("/catalog/42")
    assert route.matches_api_path("/orders/73/details")


def test_path_cache_is_bounded_when_request_paths_keep_changing():
    _path_segments.cache_clear()
    try:
        for index in range(1500):
            assert _path_segments(f"/catalog/{index}") == ("catalog", str(index))
        assert _path_segments.cache_info().currsize == 1024
        # Eviction must only cause recomputation, preserving earlier results.
        assert _path_segments("/catalog/0") == ("catalog", "0")
    finally:
        _path_segments.cache_clear()
