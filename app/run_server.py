from __future__ import annotations

import json
import os
from ipaddress import ip_network
from pathlib import Path
from string import Template

import uvicorn


def _prepare_runtime_config() -> None:
    source = os.getenv("APIM_CONFIG_SOURCE_PATH", "").strip()
    target = os.getenv("APIM_CONFIG_PATH", "").strip()

    if not source or not target or source == target:
        return

    source_path = Path(source)
    if not source_path.exists():
        raise FileNotFoundError(f"APIM_CONFIG_SOURCE_PATH does not exist: {source_path}")

    target_path = Path(target)
    if target_path.exists():
        return

    rendered = source_path.read_text(encoding="utf-8")
    if os.getenv("APIM_CONFIG_TEMPLATE_SUBSTITUTE", "").strip().lower() in {"1", "true", "yes", "on"}:
        rendered = Template(rendered).safe_substitute(os.environ)

    rendered = _anchor_fragment_directory(rendered, source_path.parent)

    proxy_cidr = os.getenv("APIM_CONFIG_TRUSTED_PROXY_CIDR", "").strip()
    if proxy_cidr:
        config = json.loads(rendered)
        network = config.setdefault("network_security", {})
        proxies = network.setdefault("trusted_proxy_cidrs", [])
        cidr = str(ip_network(proxy_cidr, strict=False))
        if cidr not in proxies:
            proxies.append(cidr)
        rendered = json.dumps(config)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(rendered, encoding="utf-8")


def _anchor_fragment_directory(rendered: str, source_dir: Path) -> str:
    """Keep a relative policy_fragments_directory pointing beside the source config after staging."""
    if "policy_fragments_directory" not in rendered:
        return rendered
    config = json.loads(rendered)
    directory = config.get("policy_fragments_directory")
    if not isinstance(directory, str) or not directory or Path(directory).is_absolute():
        return rendered
    config["policy_fragments_directory"] = str((source_dir / directory).resolve())
    return json.dumps(config)


def main() -> None:
    _prepare_runtime_config()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        access_log=False,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
