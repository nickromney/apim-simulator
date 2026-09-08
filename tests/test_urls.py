from __future__ import annotations

from urllib.parse import urlunsplit

from app.urls import http_url, https_url


def test_http_url_handles_host_only_targets() -> None:
    assert http_url("localhost:8000") == urlunsplit(("http", "localhost:8000", "", "", ""))


def test_http_url_handles_path_and_query_targets() -> None:
    assert http_url("localhost:8000/api/health?name=team") == urlunsplit(
        ("http", "localhost:8000", "/api/health", "name=team", "")
    )


def test_https_url_handles_path_and_query_targets() -> None:
    assert https_url("edge.apim.127.0.0.1.sslip.io:9443/api/health?name=team") == urlunsplit(
        ("https", "edge.apim.127.0.0.1.sslip.io:9443", "/api/health", "name=team", "")
    )


def test_a_bare_trailing_slash_is_dropped() -> None:
    """The split is only observable at the edges: everything else round-trips.

    Mutation testing named these two. `_split_target` takes a target apart and
    `urlunsplit` puts it straight back together, so for an ordinary target a
    mutant that splits on the wrong separator still produces the same URL. The
    difference only shows where a separator carries no content after it.
    """
    assert http_url("localhost:8000/") == "http://localhost:8000"


def test_an_empty_query_is_dropped() -> None:
    assert http_url("localhost:8000/api/health?") == "http://localhost:8000/api/health"
