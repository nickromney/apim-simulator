from __future__ import annotations

import json

from app import run_server


def test_prepare_runtime_config_substitutes_env_vars_when_enabled(monkeypatch, tmp_path) -> None:
    source = tmp_path / "config.template.json"
    target = tmp_path / "rendered" / "config.json"
    source.write_text('{"issuer":"${OIDC_ISSUER_EXTERNAL}"}', encoding="utf-8")

    monkeypatch.setenv("APIM_CONFIG_SOURCE_PATH", str(source))
    monkeypatch.setenv("APIM_CONFIG_PATH", str(target))
    monkeypatch.setenv("APIM_CONFIG_TEMPLATE_SUBSTITUTE", "true")
    monkeypatch.setenv("OIDC_ISSUER_EXTERNAL", "http://localhost:8180/realms/demo")

    run_server._prepare_runtime_config()

    assert target.read_text(encoding="utf-8") == ('{"issuer":"http://localhost:8180/realms/demo"}')


def test_prepare_runtime_config_adds_trusted_proxy_to_seed_only(monkeypatch, tmp_path) -> None:
    source = tmp_path / "seed.json"
    target = tmp_path / "runtime.json"
    seed = {"network_security": {"trusted_proxy_cidrs": ["192.0.2.1/32"], "private_peer_cidrs": ["192.0.2.0/24"]}}
    source.write_text(json.dumps(seed))
    monkeypatch.setenv("APIM_CONFIG_SOURCE_PATH", str(source))
    monkeypatch.setenv("APIM_CONFIG_PATH", str(target))
    monkeypatch.setenv("APIM_CONFIG_TRUSTED_PROXY_CIDR", "192.0.2.2/32")

    run_server._prepare_runtime_config()

    assert json.loads(source.read_text()) == seed
    assert json.loads(target.read_text())["network_security"] == {
        "trusted_proxy_cidrs": ["192.0.2.1/32", "192.0.2.2/32"],
        "private_peer_cidrs": ["192.0.2.0/24"],
    }
    monkeypatch.setenv("APIM_CONFIG_TRUSTED_PROXY_CIDR", "192.0.2.3/32")
    run_server._prepare_runtime_config()
    assert json.loads(target.read_text())["network_security"]["trusted_proxy_cidrs"] == ["192.0.2.1/32", "192.0.2.2/32"]
