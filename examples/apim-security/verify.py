"""Exercise local security controls through published TLS localhost ports."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import ssl
import subprocess
import time
from pathlib import Path

import httpx
import jwt

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / ".runtime"
CERTS = RUNTIME / "certs"
BASE = f"https://localhost:{os.getenv('SECURITY_GATEWAY_PORT', '8943')}"
results = []
keys = dict(line.split("=", 1) for line in (RUNTIME / "lab.env").read_text().splitlines())


def record(name, condition, detail=""):
    results.append({"check": name, "passed": bool(condition), "detail": detail})
    if not condition:
        raise AssertionError(f"{name}: {detail}")


def identity(role="contributor", **claims):
    now = int(time.time())
    payload = {
        "iss": "https://apim.local/management",
        "aud": "apim-management",
        "sub": "local-security-operator",
        "roles": [role],
        "iat": now,
        "exp": now + 300,
        "amr": ["mfa"],
        "device_compliant": True,
        **claims,
    }
    return {"Authorization": "Bearer " + jwt.encode(payload, keys["APIM_CONTROL_PLANE_SIGNING_KEY"], algorithm="HS256")}


def compose(*args):
    subprocess.run(
        ["docker", "compose", "-p", "apim-simulator-security", "-f", str(ROOT / "compose.yml"), *args],
        check=True,
        capture_output=True,
    )


def main():
    context = ssl.create_default_context(cafile=str(CERTS / "ca.pem"))
    context.load_cert_chain(str(CERTS / "browser.pem"), str(CERTS / "browser-key.pem"))
    with httpx.Client(verify=context, base_url=BASE, timeout=10, trust_env=False) as client:
        for _attempt in range(30):
            try:
                if client.get("/apim/health").status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(1)
        record("tls-host-port", client.get("/apim/health").status_code == 200)
        response = client.post("/secure/echo", json={"payload": "preserved"}, headers={"X-Apim-Trace": "true"})
        record("signed-workload-and-backend-mtls", response.status_code == 200, response.text)
        record("request-body-preserved", response.json().get("body") == {"payload": "preserved"})
        original_vault = (RUNTIME / "vault" / "secrets.json").read_text()
        rotated = "rotated-disposable-local-vault-value"
        rotated_values = json.loads(original_vault)
        rotated_values["backend-secret"] = rotated
        (RUNTIME / "vault" / "secrets.json").write_text(json.dumps(rotated_values))
        try:
            rotated_response = client.post("/secure/echo", json={"rotation": True})
            record(
                "signed-vault-secret-rotation",
                rotated_response.status_code == 200
                and rotated_response.json().get("vault_secret_sha256") == hashlib.sha256(rotated.encode()).hexdigest(),
                rotated_response.text,
            )
        finally:
            (RUNTIME / "vault" / "secrets.json").write_text(original_vault)
        vault_values = json.loads(original_vault)
        vault_values["gateway-certificate"] = base64.b64encode(
            (CERTS / "gateway-client-rotated.p12").read_bytes()
        ).decode()
        (RUNTIME / "vault" / "secrets.json").write_text(json.dumps(vault_values))
        try:
            rotated_certificate = client.post("/secure/echo", json={"certificate_rotation": True})
            record(
                "signed-vault-certificate-rotation",
                rotated_certificate.status_code == 200
                and rotated_certificate.json().get("client_thumbprint") != response.json().get("client_thumbprint"),
                rotated_certificate.text,
            )
        finally:
            (RUNTIME / "vault" / "secrets.json").write_text(original_vault)
        record("public-trace-denied", not any("trace" in key.lower() for key in response.headers))
        record("reload-anonymous-denied", client.post("/apim/reload").status_code == 401)
        record(
            "reader-write-denied",
            client.put(
                "/apim/management/apis/secure",
                headers=identity("reader"),
                json={"path": "secure", "upstream_base_url": "https://secure-backend-private:8443"},
            ).status_code
            == 403,
        )
        record(
            "administrator-mfa-required",
            client.get("/apim/management/status", headers=identity(amr=["pwd"])).status_code == 403,
        )
        record(
            "administrator-device-required",
            client.get("/apim/management/status", headers=identity(device_compliant=False)).status_code == 403,
        )
        record("waf-script-denied", client.post("/secure/echo", content="<script>alert(1)</script>").status_code == 403)
        record("bounded-body-denied", client.post("/secure/echo", content="x" * 4097).status_code == 413)
        record(
            "delete-lock-enforced", client.delete("/apim/management/apis/secure", headers=identity()).status_code == 409
        )
        backup = client.post("/apim/security/backups", headers=identity())
        record(
            "encrypted-backup",
            backup.status_code == 200 and backup.json().get("format") == "apim-encrypted-config-v1",
            backup.text[:100],
        )
        record(
            "backup-hides-secrets",
            keys["APIM_CONTROL_PLANE_SIGNING_KEY"] not in backup.text and '"backends"' not in backup.text,
        )
        record(
            "restore-valid-snapshot",
            client.post("/apim/security/restore", headers=identity(), json=backup.json()).status_code == 200,
        )
        record(
            "operator-restore-denied",
            client.post("/apim/security/restore", headers=identity("operator"), json=backup.json()).status_code == 403,
        )
        events = client.get("/apim/management/security/events", headers=identity())
        record(
            "attributed-durable-events",
            events.status_code == 200 and "local-security-operator" in events.text,
            events.text[:150],
        )
        compose("stop", "gateway-a")
        try:
            healthy = [client.post("/secure/echo", json={"failover": attempt}).status_code for attempt in range(5)]
            record("local-region-failover", all(code == 200 for code in healthy), str(healthy))
        finally:
            compose("start", "gateway-a")
    with httpx.Client(
        verify=ssl.create_default_context(cafile=str(CERTS / "ca.pem")), base_url=BASE, timeout=5, trust_env=False
    ) as anonymous:
        missing = anonymous.post("/secure/echo", json={})
        record("incoming-client-certificate-required", missing.status_code == 403, missing.text)
        forged = anonymous.post(
            "/secure/echo",
            json={},
            headers={"X-Client-Cert-Thumbprint": "trusted-marker", "X-Forwarded-Proto": "https"},
        )
        record("forged-certificate-metadata-denied", forged.status_code == 403, forged.text)
    # The backend's localhost port is protected by real TLS client certificates.
    direct_context = ssl.create_default_context(cafile=str(CERTS / "ca.pem"))
    with httpx.Client(verify=direct_context, timeout=5, trust_env=False) as direct:
        try:
            response = direct.get(f"https://localhost:{os.getenv('SECURITY_BACKEND_PORT', '8944')}/echo")
            denied = response.status_code >= 400
        except httpx.TransportError:
            denied = True
        record("backend-direct-access-requires-client-certificate", denied)
    output = {"date": "2026-10-02", "base_url": "https://localhost:<SECURITY_GATEWAY_PORT>", "checks": results}
    destination = Path("docs/security/live-results-2026-10-02.json")
    destination.write_text(json.dumps(output, indent=2) + "\n")
    print(f"Passed {len(results)} local security checks")


if __name__ == "__main__":
    main()
