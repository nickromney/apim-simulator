# APIM Simulator Walkthrough: Hello Starter With Subscription

Generated from a live run against the local repository.

`make up-hello-subscription` adds APIM product protection and the demo subscription key checks.

```bash
set -euo pipefail
make down >/dev/null 2>&1 || true
log="$(mktemp)"
make up-hello-subscription >"$log" 2>&1 || { cat "$log"; exit 1; }
for _ in $(seq 1 90); do
  curl -fsS http://localhost:8000/apim/health >/dev/null 2>&1 && break
  sleep 1
done
docker compose -f compose.yml -f compose.public.yml -f compose.hello.yml ps --format json | jq -sS .
rm -f "$log"

```

```output
[
  {
    "Command": "\"/app/.venv/bin/pyth…\"",
    "CreatedAt": "2026-04-14 10:44:44 +0100 BST",
    "ExitCode": 0,
    "Health": "",
    "ID": "f330cadfd2e5",
    "Image": "apim-simulator:latest",
    "Labels": "com.docker.dhi.entitlement=public,com.docker.dhi.flavor=,com.docker.dhi.name=dhi/python,com.docker.dhi.shell=,com.docker.dhi.title=Python 3.13.x,com.docker.dhi.url=https://dhi.io/catalog/python,com.docker.dhi.variant=runtime,com.docker.dhi.version=3.13.13-debian13,com.docker.compose.project=apim-simulator,com.docker.dhi.compliance=cis,com.docker.dhi.date.release=2024-10-07,desktop.docker.io/ports.scheme=v2,com.docker.compose.container-number=1,com.docker.compose.oneoff=False,com.docker.compose.service=apim-simulator,com.docker.dhi.date.end-of-life=2029-10-31,com.docker.dhi.definition=image/python/debian-13/3.13,com.docker.dhi.distro=debian-13,com.docker.dhi.package-manager=,desktop.docker.io/ports/8000/tcp=:8000,com.docker.compose.config-hash=5e0f1b57733ee6c34e55ab32920ae08d87d724f52119f96a617a1d377eb338ee,com.docker.compose.depends_on=hello-api:service_healthy:false,mock-backend:service_started:false,com.docker.compose.image=sha256:e288acb02f5071794dd728e5f7ed90bd7f5a3bc209cd41560186a3fb4103e69c,com.docker.compose.project.config_files=./compose.yml,./compose.public.yml,./compose.hello.yml,com.docker.compose.project.working_dir=.,com.docker.compose.version=5.1.1,com.docker.dhi.chain-id=sha256:b9e7c6e6bf9a389eaa805f1244eea298c7ecd133127518ede00ede39add3df83,com.docker.dhi.created=2026-04-11T22:45:39Z",
    "LocalVolumes": "0",
    "Mounts": "",
    "Name": "apim-simulator-apim-simulator-1",
    "Names": "apim-simulator-apim-simulator-1",
    "Networks": "apim-simulator_apim",
    "Ports": "0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp",
    "Project": "apim-simulator",
    "Publishers": [
      {
        "Protocol": "tcp",
        "PublishedPort": 8000,
        "TargetPort": 8000,
        "URL": "0.0.0.0"
      },
      {
        "Protocol": "tcp",
        "PublishedPort": 8000,
        "TargetPort": 8000,
        "URL": "::"
      }
    ],
    "RunningFor": "9 seconds ago",
    "Service": "apim-simulator",
    "Size": "0B",
    "State": "running",
    "Status": "Up 2 seconds"
  },
  {
    "Command": "\"/app/.venv/bin/uvic…\"",
    "CreatedAt": "2026-04-14 10:44:44 +0100 BST",
    "ExitCode": 0,
    "Health": "healthy",
    "ID": "52c78d5092ff",
    "Image": "apim-simulator-hello-api:latest",
    "Labels": "com.docker.dhi.date.release=2024-10-07,com.docker.dhi.flavor=,com.docker.dhi.shell=,com.docker.compose.oneoff=False,com.docker.compose.project.working_dir=.,com.docker.dhi.date.end-of-life=2029-10-31,com.docker.dhi.distro=debian-13,com.docker.dhi.name=dhi/python,com.docker.compose.config-hash=67f3fb3e730931d9496f989084f451727082e3f09840e8df8a4194d43f2b4014,com.docker.compose.image=sha256:4c88a4b676005e27d71dbc956f168a5bd27ea4dc3c295de1df7983b7605b1cdc,com.docker.dhi.compliance=cis,com.docker.dhi.entitlement=public,com.docker.dhi.title=Python 3.13.x,com.docker.dhi.url=https://dhi.io/catalog/python,com.docker.dhi.version=3.13.13-debian13,desktop.docker.io/ports.scheme=v2,com.docker.compose.container-number=1,com.docker.compose.project=apim-simulator,com.docker.compose.version=5.1.1,com.docker.dhi.definition=image/python/debian-13/3.13,com.docker.dhi.package-manager=,com.docker.dhi.variant=runtime,com.docker.compose.depends_on=,com.docker.compose.project.config_files=./compose.yml,./compose.public.yml,./compose.hello.yml,com.docker.compose.service=hello-api,com.docker.dhi.chain-id=sha256:b9e7c6e6bf9a389eaa805f1244eea298c7ecd133127518ede00ede39add3df83,com.docker.dhi.created=2026-04-11T22:45:39Z",
    "LocalVolumes": "0",
    "Mounts": "",
    "Name": "apim-simulator-hello-api-1",
    "Names": "apim-simulator-hello-api-1",
    "Networks": "apim-simulator_apim",
    "Ports": "8000/tcp",
    "Project": "apim-simulator",
    "Publishers": [
      {
        "Protocol": "tcp",
        "PublishedPort": 0,
        "TargetPort": 8000,
        "URL": ""
      }
    ],
    "RunningFor": "9 seconds ago",
    "Service": "hello-api",
    "Size": "0B",
    "State": "running",
    "Status": "Up 8 seconds (healthy)"
  },
  {
    "Command": "\"python server.py\"",
    "CreatedAt": "2026-04-14 10:44:44 +0100 BST",
    "ExitCode": 0,
    "Health": "",
    "ID": "fd7085a046a8",
    "Image": "apim-simulator-mock-backend:latest",
    "Labels": "com.docker.dhi.date.release=2024-10-07,com.docker.dhi.definition=image/python/debian-13/3.13,com.docker.dhi.flavor=,com.docker.dhi.name=dhi/python,com.docker.compose.image=sha256:32c2e331c750a4323d93200492f0773100f695d331161ef9ab266a4e6e8bc4d4,com.docker.dhi.chain-id=sha256:e68172a1b009e121980466426bb3c0b7a6184cc9d5a4200b57ee2dc4292779da,com.docker.dhi.compliance=cis,com.docker.dhi.date.end-of-life=2029-10-31,com.docker.dhi.shell=,com.docker.dhi.url=https://dhi.io/catalog/python,com.docker.dhi.version=3.13.12-debian13,com.docker.compose.service=mock-backend,com.docker.dhi.title=Python 3.13.x,com.docker.dhi.variant=runtime,desktop.docker.io/ports.scheme=v2,com.docker.compose.config-hash=815e10da41b3af6176108902ed23ffc47edfaf9c27a33e160094fa0330e92164,com.docker.compose.depends_on=,com.docker.compose.project=apim-simulator,com.docker.compose.version=5.1.1,com.docker.dhi.created=2026-04-08T03:14:50Z,com.docker.dhi.distro=debian-13,com.docker.dhi.entitlement=public,com.docker.dhi.package-manager=,com.docker.compose.container-number=1,com.docker.compose.oneoff=False,com.docker.compose.project.config_files=./compose.yml,./compose.public.yml,./compose.hello.yml,com.docker.compose.project.working_dir=.",
    "LocalVolumes": "0",
    "Mounts": "",
    "Name": "apim-simulator-mock-backend-1",
    "Names": "apim-simulator-mock-backend-1",
    "Networks": "apim-simulator_apim",
    "Ports": "8080/tcp",
    "Project": "apim-simulator",
    "Publishers": [
      {
        "Protocol": "tcp",
        "PublishedPort": 0,
        "TargetPort": 8080,
        "URL": ""
      }
    ],
    "RunningFor": "9 seconds ago",
    "Service": "mock-backend",
    "Size": "0B",
    "State": "running",
    "Status": "Up 8 seconds"
  }
]
```

```bash
set -euo pipefail
uv run --project . python - <<'PY'
import json
import httpx

client = httpx.Client(timeout=10.0)
missing = client.get("http://localhost:8000/api/health")
invalid = client.get(
    "http://localhost:8000/api/health",
    headers={"Ocp-Apim-Subscription-Key": "hello-demo-key-invalid"},
)
valid = client.get(
    "http://localhost:8000/api/hello?name=subscription",
    headers={"Ocp-Apim-Subscription-Key": "hello-demo-key"},
)
valid.raise_for_status()
summary = {
    "missing_subscription": {"status": missing.status_code, "body": missing.json()},
    "invalid_subscription": {"status": invalid.status_code, "body": invalid.json()},
    "valid_subscription": {
        "status": valid.status_code,
        "policy_header": valid.headers.get("x-hello-policy"),
        "body": valid.json(),
    },
}
print(json.dumps(summary, indent=2, sort_keys=True))
PY

```

```output
{
  "invalid_subscription": {
    "body": {
      "detail": "Invalid subscription key"
    },
    "status": 401
  },
  "missing_subscription": {
    "body": {
      "detail": "Missing subscription key"
    },
    "status": 401
  },
  "valid_subscription": {
    "body": {
      "message": "hello, subscription"
    },
    "policy_header": "applied",
    "status": 200
  }
}
```
