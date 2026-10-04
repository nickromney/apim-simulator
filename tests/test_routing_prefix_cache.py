"""API eligibility caching observes current strings and preserves prefix rules."""

import pytest

import app.config as config
from app.config import RouteConfig, _matches_api_path


def _route(prefix):
    return RouteConfig(name="catalog", path_prefix=prefix, upstream_base_url="http://backend.test")


@pytest.mark.parametrize(
    ("prefix", "path", "expected"),
    [
        ("/catalog", "/catalog/items", True),
        ("/catalog", "/catalogue/items", False),
        ("/catalog/", "///CATALOG/items///", True),
        ("catalog", "catalog/items", True),
        ("/straße", "/STRASSE/items", True),
        ("/İ", "/i/items", False),
        ("/", "", True),
        ("", "/anything", True),
        ("/catalog//items", "/catalog/items", False),
    ],
)
def test_api_prefix_cache_preserves_matching_rules(prefix, path, expected):
    route = _route(prefix)
    assert route.matches_api_path(path) is expected
    assert route.matches_api_path(path) is expected


def test_api_prefix_cache_shares_positive_and_negative_results(monkeypatch):
    _matches_api_path.cache_clear()
    calls = []
    original = config._match_path_prefix

    def tracked(prefix, path):
        calls.append((prefix, path))
        return original(prefix, path)

    monkeypatch.setattr(config, "_match_path_prefix", tracked)
    try:
        for route in (_route("/catalog"), _route("/catalog")):
            assert route.matches_api_path("/catalog/items")
            assert not route.matches_api_path("/catalogue/items")
        assert len(calls) == 2
    finally:
        _matches_api_path.cache_clear()


def test_api_prefix_cache_observes_effective_prefix_and_request_path_changes():
    route = _route("/catalog")
    assert route.matches_api_path("/catalog/items")
    assert not route.matches_api_path("/orders/items")
    route.path_prefix = "/orders"
    assert not route.matches_api_path("/catalog/items")
    assert route.matches_api_path("/orders/items")
    route.api_path_prefix = "/catalog"
    assert route.matches_api_path("/catalog/items")
    assert not route.matches_api_path("/orders/items")
    route.api_path_prefix = ""
    assert route.matches_api_path("/orders/items")


def test_api_prefix_cache_bounds_unique_pairs_and_recomputes_evicted_results():
    _matches_api_path.cache_clear()
    try:
        for index in range(1500):
            assert _matches_api_path("/catalog", f"/catalog/{index}")
        assert _matches_api_path.cache_info().currsize == 1024
        assert _matches_api_path("/catalog", "/catalog/0")
        assert not _matches_api_path("/orders", "/catalog/0")
    finally:
        _matches_api_path.cache_clear()
