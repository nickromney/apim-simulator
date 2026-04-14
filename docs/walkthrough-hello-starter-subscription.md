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
    "CreatedAt": "2026-04-14 16:53:23 +0100 BST",
    "ExitCode": 0,
    "Health": "",
    "ID": "5d48e14395ee",
    "Image": "apim-simulator:latest",
    "Labels": "com.docker.dhi.compliance=cis,com.docker.dhi.definition=image/python/debian-13/3.13,com.docker.dhi.flavor=,com.docker.dhi.package-manager=,com.docker.dhi.url=https://dhi.io/catalog/python,com.docker.compose.project.config_files=./compose.yml,./compose.public.yml,./compose.hello.yml,com.docker.compose.version=5.1.1,com.docker.dhi.chain-id=sha256:b9e7c6e6bf9a389eaa805f1244eea298c7ecd133127518ede00ede39add3df83,com.docker.dhi.created=2026-04-11T22:45:39Z,com.docker.dhi.distro=debian-13,com.docker.dhi.entitlement=public,com.docker.dhi.shell=,com.docker.dhi.title=Python 3.13.x,com.docker.compose.config-hash=5e0f1b57733ee6c34e55ab32920ae08d87d724f52119f96a617a1d377eb338ee,com.docker.compose.oneoff=False,com.docker.compose.project=apim-simulator,com.docker.compose.project.working_dir=.,com.docker.dhi.date.end-of-life=2029-10-31,com.docker.dhi.date.release=2024-10-07,com.docker.dhi.name=dhi/python,com.docker.dhi.version=3.13.13-debian13,com.docker.compose.depends_on=mock-backend:service_started:false,hello-api:service_healthy:false,com.docker.dhi.variant=runtime,desktop.docker.io/ports.scheme=v2,desktop.docker.io/ports/8000/tcp=:8000,com.docker.compose.container-number=1,com.docker.compose.image=sha256:ebb5cab01907e18284ddde73f8ab9031ee097b60b1ebfa8d28f11326ebbc6745,com.docker.compose.service=apim-simulator",
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
    "CreatedAt": "2026-04-14 16:53:23 +0100 BST",
    "ExitCode": 0,
    "Health": "healthy",
    "ID": "16b046ad2050",
    "Image": "apim-simulator-hello-api:latest",
    "Labels": "com.docker.dhi.created=2026-04-11T22:45:39Z,com.docker.dhi.package-manager=,com.docker.compose.image=sha256:5622217f8e912beaad608a333ac413ebb6e33b3915c6f1a4b9966a6bd3392a42,com.docker.compose.project=apim-simulator,com.docker.compose.project.working_dir=.,com.docker.dhi.chain-id=sha256:b9e7c6e6bf9a389eaa805f1244eea298c7ecd133127518ede00ede39add3df83,com.docker.dhi.definition=image/python/debian-13/3.13,com.docker.dhi.name=dhi/python,com.docker.dhi.version=3.13.13-debian13,desktop.docker.io/ports.scheme=v2,com.docker.compose.config-hash=67f3fb3e730931d9496f989084f451727082e3f09840e8df8a4194d43f2b4014,com.docker.compose.depends_on=,com.docker.compose.project.config_files=./compose.yml,./compose.public.yml,./compose.hello.yml,com.docker.compose.version=5.1.1,com.docker.dhi.compliance=cis,com.docker.dhi.date.end-of-life=2029-10-31,com.docker.dhi.date.release=2024-10-07,com.docker.dhi.entitlement=public,com.docker.compose.container-number=1,com.docker.dhi.distro=debian-13,com.docker.dhi.flavor=,com.docker.dhi.shell=,com.docker.dhi.title=Python 3.13.x,com.docker.dhi.url=https://dhi.io/catalog/python,com.docker.dhi.variant=runtime,com.docker.compose.oneoff=False,com.docker.compose.service=hello-api",
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
    "Status": "Up 7 seconds (healthy)"
  },
  {
    "Command": "\"python server.py\"",
    "CreatedAt": "2026-04-14 16:53:23 +0100 BST",
    "ExitCode": 0,
    "Health": "",
    "ID": "176375dc2a41",
    "Image": "apim-simulator-mock-backend:latest",
    "Labels": "com.docker.dhi.name=dhi/python,com.docker.dhi.title=Python 3.13.x,com.docker.dhi.variant=runtime,com.docker.compose.depends_on=,com.docker.compose.service=mock-backend,com.docker.dhi.date.end-of-life=2029-10-31,com.docker.dhi.entitlement=public,com.docker.dhi.version=3.13.12-debian13,desktop.docker.io/ports.scheme=v2,com.docker.compose.config-hash=815e10da41b3af6176108902ed23ffc47edfaf9c27a33e160094fa0330e92164,com.docker.compose.oneoff=False,com.docker.compose.project=apim-simulator,com.docker.compose.project.working_dir=.,com.docker.compose.version=5.1.1,com.docker.dhi.chain-id=sha256:e68172a1b009e121980466426bb3c0b7a6184cc9d5a4200b57ee2dc4292779da,com.docker.dhi.flavor=,com.docker.dhi.url=https://dhi.io/catalog/python,com.docker.compose.project.config_files=./compose.yml,./compose.public.yml,./compose.hello.yml,com.docker.dhi.compliance=cis,com.docker.dhi.created=2026-04-08T03:14:50Z,com.docker.dhi.date.release=2024-10-07,com.docker.dhi.definition=image/python/debian-13/3.13,com.docker.dhi.distro=debian-13,com.docker.dhi.package-manager=,com.docker.dhi.shell=,com.docker.compose.container-number=1,com.docker.compose.image=sha256:7be9cc30cfdcc77e147819d6028704954a317ff30622cf7c966bfa5cf8f63060",
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
    "Status": "Up 7 seconds"
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
