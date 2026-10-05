"""Concurrent eviction must preserve routing output and parameter ownership."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

import app.config as config
from app.config import RouteConfig
from scripts.benchmark_cache_modes import cache_mode


@pytest.mark.parametrize("mode", ["cached", "uncached"])
def test_concurrent_clear_keeps_changed_route_keys_and_owned_parameters(mode):
    barrier = Barrier(5, timeout=5)
    segments = config._path_segments
    prefixes = config._matches_api_path

    def worker(worker_id):
        barrier.wait()
        observed = []
        # Each worker owns its route; only pure process caches are shared.
        route = RouteConfig(
            name="synthetic",
            path_prefix="/old",
            api_path_prefix="/old",
            url_template="/{id}",
            upstream_base_url="http://backend.test",
        )
        for generation in range(40):
            prefix = f"/catalog/{worker_id}/{generation}"
            route.path_prefix = prefix
            route.api_path_prefix = prefix
            identifier = f"item-{generation}"
            path = f"{prefix}/{identifier}"
            match = route.match(method="GET", path=path, query={})
            assert match is not None
            observed.append((route.matches_api_path(path), dict(match.parameters)))
            match.parameters["injected"] = "must not leak"
            # Every worker has warmed its route before another thread clears it.
            barrier.wait()
            barrier.wait()
            again = route.match(method="GET", path=path, query={})
            assert again is not None and again.parameters == {"id": identifier}
            assert route.match(method="GET", path="/old/obsolete", query={}) is None
        return observed

    def invalidate():
        barrier.wait()
        for _ in range(40):
            barrier.wait()
            segments.cache_clear()
            prefixes.cache_clear()
            barrier.wait()

    with cache_mode(mode), ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(worker, index) for index in range(4)]
        clearing = pool.submit(invalidate)
        expected = [(True, {"id": f"item-{generation}"}) for generation in range(40)]
        assert [future.result() for future in futures] == [expected] * 4
        clearing.result()


def test_uncached_mode_restores_helpers_after_failure():
    original = config._path_segments
    with pytest.raises(RuntimeError), cache_mode("uncached"):
        assert config._path_segments is original.__wrapped__
        raise RuntimeError("synthetic failure")
    assert config._path_segments is original


@pytest.mark.parametrize("mode", ["cached", "uncached"])
@pytest.mark.parametrize("kind", ["policy", "route"])
def test_cache_modes_match_frozen_gateway_goldens(mode, kind, tmp_path, monkeypatch):
    import sys
    from pathlib import Path

    from scripts.golden_policy_outputs import main as policy_outputs
    from scripts.golden_route_outputs import main as route_outputs

    output = tmp_path / f"{kind}.json"
    monkeypatch.setattr(sys, "argv", ["golden", "--output", str(output)])
    with cache_mode(mode):
        (policy_outputs if kind == "policy" else route_outputs)()
    assert output.read_bytes() == Path(f"tests/fixtures/performance/{kind}_outputs.json").read_bytes()
