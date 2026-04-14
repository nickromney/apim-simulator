# APIM Simulator Walkthrough: Edge HTTP

Generated from a live run against the local repository.

`make up-edge` terminates through the nginx edge proxy on `apim.localtest.me:8088` and verifies forwarded-host behavior before the request reaches APIM and the MCP backend.

```bash
set -euo pipefail
make down >/dev/null 2>&1 || true
log="$(mktemp)"
make up-edge >"$log" 2>&1 || { cat "$log"; exit 1; }
for _ in $(seq 1 90); do
  curl -fsS http://apim.localtest.me:8088/apim/health >/dev/null 2>&1 && break
  sleep 1
done
docker compose -f compose.yml -f compose.edge.yml -f compose.mcp.yml ps --format json | jq -sS .
rm -f "$log"

```

```output
[
  {
    "Command": "\"/app/.venv/bin/pyth…\"",
    "CreatedAt": "2026-04-14 10:43:47 +0100 BST",
    "ExitCode": 0,
    "Health": "",
    "ID": "5b018b48b83a",
    "Image": "apim-simulator:latest",
    "Labels": "com.docker.compose.project=apim-simulator,com.docker.dhi.chain-id=sha256:b9e7c6e6bf9a389eaa805f1244eea298c7ecd133127518ede00ede39add3df83,com.docker.dhi.distro=debian-13,com.docker.dhi.entitlement=public,com.docker.dhi.title=Python 3.13.x,desktop.docker.io/ports.scheme=v2,com.docker.compose.container-number=1,com.docker.compose.service=apim-simulator,com.docker.compose.version=5.1.1,com.docker.dhi.compliance=cis,com.docker.dhi.created=2026-04-11T22:45:39Z,com.docker.dhi.date.release=2024-10-07,com.docker.dhi.definition=image/python/debian-13/3.13,com.docker.dhi.flavor=,com.docker.compose.project.working_dir=.,com.docker.dhi.name=dhi/python,com.docker.dhi.package-manager=,com.docker.dhi.shell=,com.docker.dhi.url=https://dhi.io/catalog/python,com.docker.dhi.version=3.13.13-debian13,com.docker.dhi.date.end-of-life=2029-10-31,com.docker.compose.config-hash=5c27086d953febdfac2390b708c2c51cd71042d962fb28529d6492eedc1325d0,com.docker.compose.oneoff=False,com.docker.compose.project.config_files=./compose.yml,./compose.edge.yml,./compose.mcp.yml,com.docker.dhi.variant=runtime,com.docker.compose.depends_on=mcp-server:service_started:false,mock-backend:service_started:false,com.docker.compose.image=sha256:074d8cf56f6b6ebe22b30b8c553bb79e4a5773e9713dbdbf21e243225630bc36",
    "LocalVolumes": "0",
    "Mounts": "",
    "Name": "apim-simulator-apim-simulator-1",
    "Names": "apim-simulator-apim-simulator-1",
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
    "RunningFor": "3 seconds ago",
    "Service": "apim-simulator",
    "Size": "0B",
    "State": "running",
    "Status": "Up 2 seconds"
  },
  {
    "Command": "\"nginx -g 'daemon of…\"",
    "CreatedAt": "2026-04-14 10:43:47 +0100 BST",
    "ExitCode": 0,
    "Health": "",
    "ID": "60494d34beae",
    "Image": "dhi.io/nginx:1.29.5-debian13",
    "Labels": "com.docker.compose.service=edge-proxy,com.docker.compose.version=5.1.1,com.docker.dhi.chain-id=sha256:12d6f6bfdcac60cd53148007d8b5ee2c5a827f82ad0a8568232264eb95b6f5e2,com.docker.dhi.compliance=cis,com.docker.dhi.package-manager=,com.docker.dhi.variant=runtime,com.docker.compose.depends_on=apim-simulator:service_started:false,com.docker.compose.config-hash=ca5babfe6a96c106d59ef2047b15e782aa4fa9ef4ce1e895a97824f075c9e4d1,com.docker.dhi.date.release=2025-06-24,desktop.docker.io/binds/0/SourceKind=hostFile,com.docker.compose.project.config_files=./compose.yml,./compose.edge.yml,./compose.mcp.yml,com.docker.compose.project.working_dir=.,desktop.docker.io/binds/1/Source=./examples/edge/certs,com.docker.dhi.definition=image/nginx/debian-13/mainline,com.docker.dhi.distro=debian-13,com.docker.dhi.name=dhi/nginx,com.docker.dhi.shell=,com.docker.dhi.version=1.29.5-debian13,com.docker.compose.container-number=1,com.docker.compose.image=sha256:9683af47feae3bab0031b489ed85f93f340a0f8b83a2edccc9f761dbfce1bffd,com.docker.compose.oneoff=False,com.docker.dhi.flavor=,desktop.docker.io/binds/0/Target=/etc/nginx/nginx.conf,desktop.docker.io/ports/8088/tcp=:8088,com.docker.compose.project=apim-simulator,com.docker.dhi.url=https://dhi.io/catalog/nginx,desktop.docker.io/binds/1/Target=/etc/nginx/certs,com.docker.dhi.created=2026-02-05T05:17:44Z,com.docker.dhi.title=Nginx mainline,desktop.docker.io/binds/0/Source=./examples/edge/nginx.conf,desktop.docker.io/binds/1/SourceKind=hostFile,desktop.docker.io/ports.scheme=v2",
    "LocalVolumes": "0",
    "Mounts": "/host_mnt/User…,/host_mnt/User…",
    "Name": "apim-simulator-edge-proxy-1",
    "Names": "apim-simulator-edge-proxy-1",
    "Networks": "apim-simulator_apim",
    "Ports": "0.0.0.0:8088->8088/tcp, [::]:8088->8088/tcp",
    "Project": "apim-simulator",
    "Publishers": [
      {
        "Protocol": "tcp",
        "PublishedPort": 8088,
        "TargetPort": 8088,
        "URL": "0.0.0.0"
      },
      {
        "Protocol": "tcp",
        "PublishedPort": 8088,
        "TargetPort": 8088,
        "URL": "::"
      }
    ],
    "RunningFor": "3 seconds ago",
    "Service": "edge-proxy",
    "Size": "0B",
    "State": "running",
    "Status": "Up 2 seconds"
  },
  {
    "Command": "\"python server.py\"",
    "CreatedAt": "2026-04-14 10:43:47 +0100 BST",
    "ExitCode": 0,
    "Health": "",
    "ID": "080790969af0",
    "Image": "apim-simulator-mcp-server:latest",
    "Labels": "com.docker.compose.service=mcp-server,com.docker.compose.version=5.1.1,com.docker.dhi.chain-id=sha256:b9e7c6e6bf9a389eaa805f1244eea298c7ecd133127518ede00ede39add3df83,com.docker.dhi.name=dhi/python,com.docker.compose.project.working_dir=.,com.docker.dhi.created=2026-04-11T22:45:39Z,com.docker.dhi.date.end-of-life=2029-10-31,com.docker.dhi.entitlement=public,com.docker.dhi.flavor=,com.docker.dhi.package-manager=,com.docker.dhi.url=https://dhi.io/catalog/python,com.docker.dhi.variant=runtime,com.docker.compose.config-hash=76e86f087961052eccf845b8e6e6d6491b69c60f099507b0371c12bc1cb7e490,com.docker.dhi.definition=image/python/debian-13/3.13,com.docker.dhi.title=Python 3.13.x,com.docker.dhi.version=3.13.13-debian13,desktop.docker.io/ports.scheme=v2,com.docker.compose.container-number=1,com.docker.compose.image=sha256:74f0175323884448b2c2c23941567dec3d84b4b3709e6176a70aedf92263a12c,com.docker.dhi.compliance=cis,com.docker.dhi.date.release=2024-10-07,com.docker.dhi.distro=debian-13,com.docker.dhi.shell=,com.docker.compose.depends_on=,com.docker.compose.oneoff=False,com.docker.compose.project=apim-simulator,com.docker.compose.project.config_files=./compose.yml,./compose.edge.yml,./compose.mcp.yml",
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
    "RunningFor": "3 seconds ago",
    "Service": "mcp-server",
    "Size": "0B",
    "State": "running",
    "Status": "Up 2 seconds"
  },
  {
    "Command": "\"python server.py\"",
    "CreatedAt": "2026-04-14 10:43:47 +0100 BST",
    "ExitCode": 0,
    "Health": "",
    "ID": "0f1a1cabfff9",
    "Image": "apim-simulator-mock-backend:latest",
    "Labels": "com.docker.dhi.version=3.13.12-debian13,desktop.docker.io/ports.scheme=v2,com.docker.dhi.date.end-of-life=2029-10-31,com.docker.compose.config-hash=815e10da41b3af6176108902ed23ffc47edfaf9c27a33e160094fa0330e92164,com.docker.compose.image=sha256:d147cee5d1570679f865e870ed27e4ae288101e537529af233d61442706f05f0,com.docker.compose.project=apim-simulator,com.docker.dhi.flavor=,com.docker.dhi.shell=,com.docker.dhi.title=Python 3.13.x,com.docker.compose.container-number=1,com.docker.dhi.date.release=2024-10-07,com.docker.dhi.package-manager=,com.docker.dhi.variant=runtime,com.docker.compose.oneoff=False,com.docker.compose.project.working_dir=.,com.docker.compose.version=5.1.1,com.docker.dhi.chain-id=sha256:e68172a1b009e121980466426bb3c0b7a6184cc9d5a4200b57ee2dc4292779da,com.docker.dhi.compliance=cis,com.docker.dhi.definition=image/python/debian-13/3.13,com.docker.dhi.entitlement=public,com.docker.dhi.name=dhi/python,com.docker.compose.depends_on=,com.docker.compose.project.config_files=./compose.yml,./compose.edge.yml,./compose.mcp.yml,com.docker.compose.service=mock-backend,com.docker.dhi.created=2026-04-08T03:14:50Z,com.docker.dhi.distro=debian-13,com.docker.dhi.url=https://dhi.io/catalog/python",
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
    "RunningFor": "3 seconds ago",
    "Service": "mock-backend",
    "Size": "0B",
    "State": "running",
    "Status": "Up 2 seconds"
  }
]
```

```bash
set -euo pipefail
smoke_log="$(mktemp)"
make smoke-edge >"$smoke_log" 2>&1 || { cat "$smoke_log"; exit 1; }
edge_echo="$(curl -fsS -H 'Ocp-Apim-Subscription-Key: mcp-demo-key' -H 'x-apim-trace: true' http://apim.localtest.me:8088/__edge/echo)"
jq -n \
  --argjson edge_echo "$edge_echo" \
  --arg smoke_log "$(cat "$smoke_log")" \
  '{
    edge_echo: {
      path: $edge_echo.path,
      host: $edge_echo.headers.host,
      forwarded_host: $edge_echo.headers["x-forwarded-host"],
      forwarded_proto: $edge_echo.headers["x-forwarded-proto"]
    },
    smoke_edge: "passed",
    smoke_output: ($smoke_log | split("\n") | map(select(length > 0)))
  }'
rm -f "$smoke_log"

```

```output
{
  "edge_echo": {
    "path": "/api/echo",
    "host": "apim.localtest.me:8088",
    "forwarded_host": "apim.localtest.me:8088",
    "forwarded_proto": "http"
  },
  "smoke_edge": "passed",
  "smoke_output": [
    "SMOKE_EDGE_BASE_URL=\"http://apim.localtest.me:8088\" uv run --project . --extra mcp python scripts/smoke_edge.py",
    "MCP smoke passed",
    "- server: APIM Simulator Demo MCP Server",
    "- tools: add_numbers, uppercase",
    "- add_numbers: {",
    "  \"sum\": 5",
    "}",
    "Edge smoke passed",
    "- base_url: http://apim.localtest.me:8088",
    "- forwarded_host: apim.localtest.me:8088",
    "- forwarded_proto: http"
  ]
}
```
