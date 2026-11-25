"""Prove the configuration-pull topology through published localhost ports."""

from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path

import httpx

TENANT = {"X-Apim-Tenant-Key": "local-dev-tenant-key"}
UPDATED_POLICY = (
    '<policies><inbound><set-header name="x-demo-policy" '
    'exists-action="override"><value>updated</value></set-header><base /></inbound>'
    "<backend><base /></backend><outbound><base /></outbound></policies>"
)


def _wait_for(client: httpx.Client, url: str, expected: str, timeout: float = 15) -> httpx.Response:
    deadline = time.monotonic() + timeout
    while True:
        response = client.get(url)
        if response.status_code == 200 and response.json().get("policy_header") == expected:
            return response
        if time.monotonic() >= deadline:
            raise AssertionError(f"{url}: expected policy {expected!r}, got {response.status_code} {response.text}")
        time.sleep(0.25)


def _compose(args: argparse.Namespace, *command: str) -> None:
    subprocess.run(
        [
            "docker",
            "compose",
            "--project-directory",
            str(Path(args.compose_file).parents[2]),
            "--project-name",
            args.compose_project,
            "-f",
            args.compose_file,
            *command,
        ],
        check=True,
    )


def smoke(args: argparse.Namespace) -> None:
    managed = f"{args.managed_url.rstrip('/')}/remote/"
    self_hosted = f"{args.self_hosted_url.rstrip('/')}/remote/"
    with httpx.Client(timeout=10) as client:
        status = client.get(f"{args.self_hosted_url.rstrip('/')}/apim/self-hosted-gateway/status")
        assert status.status_code == 200, status.text
        assert status.json()["successful_syncs"] >= 1, status.text
        print("ok self-hosted gateway pulled configuration")

        response = client.get(managed)
        assert response.status_code == 404, response.text
        print("ok API assigned only to self-hosted gateway is 404 on managed")

        _wait_for(client, self_hosted, "initial")
        print("ok self-hosted gateway serves the remote backend locally")

        updated = client.put(
            f"{args.managed_url.rstrip('/')}/apim/management/apis/remote",
            headers={**TENANT, "Content-Type": "application/json"},
            json={
                "path": "remote",
                "upstream_base_url": "http://remote-backend:8080",
                "gateways": ["remote"],
                "policies_xml": UPDATED_POLICY,
            },
        )
        assert updated.status_code == 200, updated.text
        _wait_for(client, self_hosted, "updated")
        print("ok managed policy change reached self-hosted gateway")

        _compose(args, "stop", "managed")
        try:
            _wait_for(client, self_hosted, "updated", timeout=8)
            deadline = time.monotonic() + 8
            while True:
                status = client.get(f"{args.self_hosted_url.rstrip('/')}/apim/self-hosted-gateway/status").json()
                if status["last_error"]:
                    break
                if time.monotonic() >= deadline:
                    raise AssertionError("self-hosted status did not record managed-instance outage")
                time.sleep(0.25)
            print("ok self-hosted gateway served last-known-good configuration while managed was stopped")
        finally:
            _compose(args, "up", "-d", "--wait", "managed")
    print("self-hosted gateway smoke passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--managed-url", default="http://127.0.0.1:8000")
    parser.add_argument("--self-hosted-url", default="http://127.0.0.1:8001")
    parser.add_argument("--compose-project", required=True)
    parser.add_argument("--compose-file", required=True)
    smoke(parser.parse_args())


if __name__ == "__main__":
    main()
