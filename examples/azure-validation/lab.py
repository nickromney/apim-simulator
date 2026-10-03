#!/usr/bin/env python3
"""Own only randomly named simulator fixtures inside an existing Azure APIM."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlsplit

import httpx
from fixtures import fixtures

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
RUNTIME = Path(__file__).parent / ".runtime"
API_VERSION = "2024-05-01"


def save(name: str, value: dict | list) -> None:
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    path = RUNTIME / name
    path.write_text(json.dumps(value, indent=2) + "\n")
    path.chmod(0o600)


def load(name: str) -> dict:
    return json.loads((RUNTIME / name).read_text())


def prepare() -> None:
    if (RUNTIME / "manifest.json").exists():
        manifest = load("manifest.json")
    else:
        manifest = {"prefix": "sim-parity-" + secrets.token_hex(4), "key": secrets.token_hex(32)}
    manifest.setdefault("secondary_key", secrets.token_hex(32))
    prefix = manifest["prefix"]
    config, cases = fixtures(prefix)
    if "PARITY_SKIP_FRAGMENTS" in os.environ:
        manifest["skip_fragments"] = os.environ["PARITY_SKIP_FRAGMENTS"] == "1"
    if manifest.get("skip_fragments"):
        config.pop("policy_fragments", None)
        config["apis"][prefix]["operations"].pop("fragment", None)
        cases = [case for case in cases if case["name"] != "fragment"]
        save(
            "excluded.json", [{"name": "fragment", "reason": "Explicitly excluded: deployment policy blocks fragments"}]
        )
    config["subscription"]["subscriptions"] = {
        prefix: {
            "id": prefix,
            "name": "Simulator parity",
            "api_id": prefix,
            "keys": {"primary": manifest["key"], "secondary": manifest["secondary_key"]},
        }
    }
    save("manifest.json", manifest)
    save("config.json", config)
    save("cases.json", cases)
    print(f"Prepared {len(cases)} cases")


def azure_client() -> tuple[httpx.Client, str]:
    service_id = os.environ["AZURE_APIM_SERVICE_ID"].rstrip("/")
    token = subprocess.check_output(
        [
            "az",
            "account",
            "get-access-token",
            "--resource",
            "https://management.azure.com/",
            "--query",
            "accessToken",
            "-o",
            "tsv",
        ],
        text=True,
    ).strip()
    return httpx.Client(
        headers={"Authorization": "Bearer " + token}, timeout=120
    ), "https://management.azure.com" + service_id


def arm(client: httpx.Client, base: str, method: str, path: str, payload: dict | None = None) -> dict:
    parts = urlsplit(path)
    result = client.request(
        method,
        base + parts.path,
        params={**dict(parse_qsl(parts.query)), "api-version": API_VERSION},
        headers={"If-Match": "*"} if method == "DELETE" else None,
        json=payload,
    )
    if method == "DELETE" and result.status_code == 404:
        return {}
    if result.is_error:
        # Save compiler errors locally; avoid printing deployment IDs or secrets.
        save("arm-error.json", {"path": path, "status": result.status_code, "detail": result.text})
        raise RuntimeError(f"ARM {method} failed with {result.status_code}; see .runtime/arm-error.json")
    return result.json() if result.content and "application/json" in result.headers.get("content-type", "") else {}


def deploy() -> None:
    prepare()
    manifest = load("manifest.json")
    prefix = manifest["prefix"]
    config = load("config.json")
    client, base = azure_client()
    with client:
        for name, named_value in config.get("named_values", {}).items():
            arm(
                client,
                base,
                "PUT",
                f"/namedValues/{quote(name, safe='')}",
                {
                    "properties": {
                        "displayName": named_value["display_name"],
                        "value": named_value["value"],
                        "secret": False,
                    }
                },
            )
        for name, fragment in config.get("policy_fragments", {}).items():
            arm(
                client,
                base,
                "PUT",
                f"/policyFragments/{quote(name, safe='')}",
                {"properties": {"format": "rawxml", "value": fragment}},
            )
        arm(
            client,
            base,
            "PUT",
            f"/apis/{prefix}",
            {
                "properties": {
                    "displayName": "APIM simulator parity fixtures",
                    "path": prefix,
                    "protocols": ["https"],
                    "subscriptionRequired": True,
                }
            },
        )
        # API-level inheritance is deliberately empty: existing service policies
        # and any production integrations never enter this isolated fixture API.
        arm(
            client,
            base,
            "PUT",
            f"/apis/{prefix}/policies/policy",
            {
                "properties": {
                    "format": "rawxml",
                    "value": "<policies><inbound /><backend /><outbound /><on-error /></policies>",
                }
            },
        )
        failures = []
        for name, operation in config["apis"][prefix]["operations"].items():
            path = f"/apis/{prefix}/operations/{quote(name, safe='')}"
            parameters = (
                [{"name": "id", "type": "string", "required": True}] if "{id}" in operation["url_template"] else []
            )
            arm(
                client,
                base,
                "PUT",
                path,
                {
                    "properties": {
                        "displayName": name,
                        "method": operation["method"],
                        "urlTemplate": operation["url_template"],
                        "templateParameters": parameters,
                    }
                },
            )
            try:
                arm(
                    client,
                    base,
                    "PUT",
                    path + "/policies/policy",
                    {"properties": {"format": "rawxml", "value": operation["policies_xml"]}},
                )
            except RuntimeError:
                failures.append({"name": name, "error": load("arm-error.json")})
            print(f"Compiled {name}: {'rejected' if failures and failures[-1]['name'] == name else 'accepted'}")
        save("compilation.json", failures)
        arm(
            client,
            base,
            "PUT",
            f"/subscriptions/{prefix}",
            {
                "properties": {
                    "displayName": "Simulator parity fixtures",
                    "scope": f"/apis/{prefix}",
                    "state": "active",
                    "primaryKey": manifest["key"],
                    "secondaryKey": manifest["secondary_key"],
                }
            },
        )
    if failures:
        raise RuntimeError(f"Azure rejected {len(failures)} policies; see .runtime/compilation.json")


def serve() -> None:
    import uvicorn

    from app.config import GatewayConfig
    from app.main import create_app

    prepare()
    uvicorn.run(
        create_app(config=GatewayConfig.model_validate(load("config.json"))),
        host="127.0.0.1",
        port=int(os.environ.get("PARITY_SIMULATOR_PORT", "18891")),
    )


def verify() -> int:
    from scripts.verify_azure import main

    prepare()
    key = load("manifest.json")["key"]
    os.environ["VERIFY_CASES"] = str(RUNTIME / "cases.json")
    os.environ["VERIFY_REPORT"] = str(RUNTIME / "report.json")
    os.environ.setdefault("SIMULATOR_BASE_URL", "http://127.0.0.1:18891")
    os.environ["SIMULATOR_HEADERS"] = json.dumps({"Ocp-Apim-Subscription-Key": key})
    os.environ["AZURE_APIM_HEADERS"] = json.dumps({"Ocp-Apim-Subscription-Key": key})
    return main()


def cleanup() -> None:
    manifest = load("manifest.json")
    prefix = manifest["prefix"]
    if not prefix.startswith("sim-parity-"):
        raise ValueError("Refusing to remove a resource outside the fixture namespace")
    client, base = azure_client()
    with client:
        arm(client, base, "DELETE", f"/subscriptions/{prefix}")
        arm(client, base, "DELETE", f"/apis/{prefix}?deleteRevisions=true")
        arm(client, base, "DELETE", f"/policyFragments/{prefix}-fragment")
        arm(client, base, "DELETE", f"/namedValues/{prefix}-greeting")
    print("Removed Azure fixture API and subscription; networking is managed separately in Terraform")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare", "deploy", "serve", "verify", "cleanup"])
    args = parser.parse_args()
    result = globals()[args.command]()
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
