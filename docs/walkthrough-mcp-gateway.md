# APIM Simulator Walkthrough: MCP Gateway

Generated from a live run against the local repository.

`make up-mcp` fronts the example MCP server through APIM and keeps the simulator’s management surface available on the same gateway.

```bash
set -euo pipefail
make down >/dev/null 2>&1 || true
log="$(mktemp)"
make up-mcp >"$log" 2>&1 || { cat "$log"; exit 1; }
for _ in $(seq 1 90); do
  curl -fsS http://localhost:8000/apim/health >/dev/null 2>&1 && break
  sleep 1
done
docker compose -f compose.yml -f compose.public.yml -f compose.mcp.yml ps --format json | jq -sS .
rm -f "$log"

```

```output
[
  {
    "Command": "\"/app/.venv/bin/pyth…\"",
    "CreatedAt": "2026-04-14 16:52:07 +0100 BST",
    "ExitCode": 0,
    "Health": "",
    "ID": "67586abc63fc",
    "Image": "apim-simulator:latest",
    "Labels": "com.docker.dhi.chain-id=sha256:b9e7c6e6bf9a389eaa805f1244eea298c7ecd133127518ede00ede39add3df83,com.docker.dhi.distro=debian-13,com.docker.dhi.package-manager=,com.docker.dhi.shell=,desktop.docker.io/ports/8000/tcp=:8000,com.docker.compose.config-hash=89d52b8bd387d59e28ac75d762420a0367a01503d09a66a17501017daf12ba82,com.docker.compose.image=sha256:0bbe4a35ef713b63158fa0670d0f1225e3e42ab78763b837cb4e0b63af388919,com.docker.compose.service=apim-simulator,com.docker.dhi.created=2026-04-11T22:45:39Z,com.docker.dhi.entitlement=public,com.docker.dhi.title=Python 3.13.x,com.docker.dhi.url=https://dhi.io/catalog/python,com.docker.dhi.version=3.13.13-debian13,com.docker.compose.project=apim-simulator,com.docker.compose.project.config_files=./compose.yml,./compose.public.yml,./compose.mcp.yml,com.docker.compose.project.working_dir=.,com.docker.compose.version=5.1.1,com.docker.dhi.compliance=cis,com.docker.dhi.date.release=2024-10-07,com.docker.dhi.definition=image/python/debian-13/3.13,com.docker.dhi.flavor=,com.docker.compose.oneoff=False,com.docker.dhi.date.end-of-life=2029-10-31,com.docker.dhi.name=dhi/python,com.docker.dhi.variant=runtime,desktop.docker.io/ports.scheme=v2,com.docker.compose.container-number=1,com.docker.compose.depends_on=mcp-server:service_started:false,mock-backend:service_started:false",
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
    "RunningFor": "4 seconds ago",
    "Service": "apim-simulator",
    "Size": "0B",
    "State": "running",
    "Status": "Up 3 seconds"
  },
  {
    "Command": "\"python server.py\"",
    "CreatedAt": "2026-04-14 16:52:07 +0100 BST",
    "ExitCode": 0,
    "Health": "",
    "ID": "b2adc8bc4eea",
    "Image": "apim-simulator-mcp-server:latest",
    "Labels": "com.docker.dhi.distro=debian-13,com.docker.dhi.entitlement=public,desktop.docker.io/ports.scheme=v2,com.docker.compose.config-hash=76e86f087961052eccf845b8e6e6d6491b69c60f099507b0371c12bc1cb7e490,com.docker.compose.project=apim-simulator,com.docker.compose.version=5.1.1,com.docker.dhi.chain-id=sha256:b9e7c6e6bf9a389eaa805f1244eea298c7ecd133127518ede00ede39add3df83,com.docker.dhi.compliance=cis,com.docker.dhi.definition=image/python/debian-13/3.13,com.docker.dhi.package-manager=,com.docker.dhi.title=Python 3.13.x,com.docker.compose.container-number=1,com.docker.compose.image=sha256:a11cd0aa4cc103eb0e1b428f794042bf280a546bc35108801e0c3852c6b71b8e,com.docker.dhi.flavor=,com.docker.dhi.url=https://dhi.io/catalog/python,com.docker.dhi.variant=runtime,com.docker.compose.oneoff=False,com.docker.compose.project.working_dir=.,com.docker.compose.service=mcp-server,com.docker.dhi.date.end-of-life=2029-10-31,com.docker.dhi.date.release=2024-10-07,com.docker.dhi.name=dhi/python,com.docker.dhi.shell=,com.docker.dhi.version=3.13.13-debian13,com.docker.compose.depends_on=,com.docker.compose.project.config_files=./compose.yml,./compose.public.yml,./compose.mcp.yml,com.docker.dhi.created=2026-04-11T22:45:39Z",
    "LocalVolumes": "0",
    "Mounts": "",
    "Name": "apim-simulator-mcp-server-1",
    "Names": "apim-simulator-mcp-server-1",
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
    "RunningFor": "4 seconds ago",
    "Service": "mcp-server",
    "Size": "0B",
    "State": "running",
    "Status": "Up 3 seconds"
  },
  {
    "Command": "\"python server.py\"",
    "CreatedAt": "2026-04-14 16:52:07 +0100 BST",
    "ExitCode": 0,
    "Health": "",
    "ID": "1a432a79d843",
    "Image": "apim-simulator-mock-backend:latest",
    "Labels": "com.docker.dhi.package-manager=,com.docker.dhi.version=3.13.12-debian13,com.docker.compose.container-number=1,com.docker.dhi.created=2026-04-08T03:14:50Z,com.docker.dhi.date.end-of-life=2029-10-31,com.docker.dhi.flavor=,com.docker.dhi.title=Python 3.13.x,desktop.docker.io/ports.scheme=v2,com.docker.compose.config-hash=815e10da41b3af6176108902ed23ffc47edfaf9c27a33e160094fa0330e92164,com.docker.compose.image=sha256:59f27077d07d87540b7fbd624ddedfa99cda81be4755792d2081d8e0f7a9c253,com.docker.compose.project.config_files=./compose.yml,./compose.public.yml,./compose.mcp.yml,com.docker.compose.project.working_dir=.,com.docker.compose.service=mock-backend,com.docker.compose.version=5.1.1,com.docker.dhi.compliance=cis,com.docker.dhi.distro=debian-13,com.docker.compose.depends_on=,com.docker.compose.oneoff=False,com.docker.compose.project=apim-simulator,com.docker.dhi.definition=image/python/debian-13/3.13,com.docker.dhi.name=dhi/python,com.docker.dhi.shell=,com.docker.dhi.url=https://dhi.io/catalog/python,com.docker.dhi.variant=runtime,com.docker.dhi.chain-id=sha256:e68172a1b009e121980466426bb3c0b7a6184cc9d5a4200b57ee2dc4292779da,com.docker.dhi.date.release=2024-10-07,com.docker.dhi.entitlement=public",
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
    "RunningFor": "4 seconds ago",
    "Service": "mock-backend",
    "Size": "0B",
    "State": "running",
    "Status": "Up 3 seconds"
  }
]
```

```bash
set -euo pipefail
smoke_log="$(mktemp)"
make smoke-mcp >"$smoke_log" 2>&1 || { cat "$smoke_log"; exit 1; }
status="$(curl -fsS -H 'X-Apim-Tenant-Key: local-dev-tenant-key' http://localhost:8000/apim/management/status)"
jq -n \
  --argjson status "$status" \
  --arg smoke_log "$(cat "$smoke_log")" \
  '{
    management: {
      service: $status.service,
      counts: $status.counts
    },
    smoke_mcp: "passed",
    smoke_output: ($smoke_log | split("\n") | map(select(length > 0)))
  }'
rm -f "$smoke_log"

```

```output
{
  "management": {
    "service": {
      "id": "service/apim-simulator",
      "name": "apim-simulator",
      "display_name": "Local APIM Simulator"
    },
    "counts": {
      "routes": 2,
      "apis": 0,
      "operations": 0,
      "api_revisions": 0,
      "api_releases": 0,
      "products": 1,
      "subscriptions": 1,
      "backends": 0,
      "named_values": 0,
      "loggers": 0,
      "diagnostics": 0,
      "api_version_sets": 0,
      "policy_fragments": 0,
      "users": 0,
      "groups": 0,
      "tags": 0,
      "recent_traces": 0
    }
  },
  "smoke_mcp": "passed",
  "smoke_output": [
    "SMOKE_MCP_URL=\"http://localhost:8000/mcp\" uv run --project . --extra mcp python scripts/smoke_mcp.py",
    "MCP smoke passed",
    "- server: APIM Simulator Demo MCP Server",
    "- tools: add_numbers, uppercase",
    "- add_numbers: {",
    "  \"sum\": 5",
    "}"
  ]
}
```
