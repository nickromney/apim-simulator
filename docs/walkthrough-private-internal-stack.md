# APIM Simulator Walkthrough: Private Internal Stack

Generated from a live run against the local repository.

The private shape intentionally does not publish `localhost:8000`. Validation happens through the internal smoke runner container instead.

```bash
set -euo pipefail
make down >/dev/null 2>&1 || true
log="$(mktemp)"
docker compose -f compose.yml -f compose.private.yml -f compose.mcp.yml up --build -d >"$log" 2>&1 || { cat "$log"; exit 1; }
docker compose -f compose.yml -f compose.private.yml -f compose.mcp.yml ps --format json | jq -sS .
rm -f "$log"

```

```output
[
  {
    "Command": "\"/app/.venv/bin/pyth…\"",
    "CreatedAt": "2026-04-14 16:52:39 +0100 BST",
    "ExitCode": 0,
    "Health": "",
    "ID": "e2c6abf48cd3",
    "Image": "apim-simulator:latest",
    "Labels": "com.docker.compose.oneoff=False,com.docker.compose.project=apim-simulator,com.docker.dhi.chain-id=sha256:b9e7c6e6bf9a389eaa805f1244eea298c7ecd133127518ede00ede39add3df83,com.docker.dhi.created=2026-04-11T22:45:39Z,com.docker.dhi.flavor=,com.docker.dhi.name=dhi/python,com.docker.dhi.shell=,com.docker.compose.config-hash=5c27086d953febdfac2390b708c2c51cd71042d962fb28529d6492eedc1325d0,com.docker.compose.service=apim-simulator,com.docker.dhi.compliance=cis,com.docker.dhi.date.end-of-life=2029-10-31,com.docker.dhi.definition=image/python/debian-13/3.13,com.docker.dhi.distro=debian-13,com.docker.dhi.entitlement=public,com.docker.dhi.package-manager=,com.docker.compose.container-number=1,com.docker.compose.image=sha256:90255f3cfe85e530e1f14cc2a382eb5aa54d7bcbc5c4f93e63a56fe8a8509ec6,com.docker.compose.project.config_files=./compose.yml,./compose.private.yml,./compose.mcp.yml,com.docker.compose.project.working_dir=.,com.docker.dhi.date.release=2024-10-07,com.docker.dhi.title=Python 3.13.x,com.docker.dhi.url=https://dhi.io/catalog/python,com.docker.dhi.variant=runtime,com.docker.compose.depends_on=mock-backend:service_started:false,mcp-server:service_started:false,com.docker.compose.version=5.1.1,com.docker.dhi.version=3.13.13-debian13,desktop.docker.io/ports.scheme=v2",
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
    "RunningFor": "Less than a second ago",
    "Service": "apim-simulator",
    "Size": "0B",
    "State": "running",
    "Status": "Up Less than a second"
  },
  {
    "Command": "\"python server.py\"",
    "CreatedAt": "2026-04-14 16:52:39 +0100 BST",
    "ExitCode": 0,
    "Health": "",
    "ID": "2abb88e6e7cb",
    "Image": "apim-simulator-mcp-server:latest",
    "Labels": "com.docker.compose.config-hash=76e86f087961052eccf845b8e6e6d6491b69c60f099507b0371c12bc1cb7e490,com.docker.compose.container-number=1,com.docker.compose.oneoff=False,com.docker.compose.service=mcp-server,com.docker.dhi.date.end-of-life=2029-10-31,com.docker.dhi.flavor=,com.docker.dhi.variant=runtime,com.docker.dhi.version=3.13.13-debian13,com.docker.compose.depends_on=,com.docker.compose.image=sha256:7e7326b6b9609bec6987d1b699d20c354ac97a30eb485f130c2e604517b10c4f,com.docker.compose.project=apim-simulator,com.docker.compose.project.config_files=./compose.yml,./compose.private.yml,./compose.mcp.yml,com.docker.dhi.chain-id=sha256:b9e7c6e6bf9a389eaa805f1244eea298c7ecd133127518ede00ede39add3df83,com.docker.dhi.date.release=2024-10-07,com.docker.dhi.definition=image/python/debian-13/3.13,com.docker.dhi.entitlement=public,com.docker.compose.project.working_dir=.,com.docker.compose.version=5.1.1,com.docker.dhi.distro=debian-13,com.docker.dhi.name=dhi/python,com.docker.dhi.package-manager=,com.docker.dhi.title=Python 3.13.x,com.docker.dhi.url=https://dhi.io/catalog/python,desktop.docker.io/ports.scheme=v2,com.docker.dhi.compliance=cis,com.docker.dhi.created=2026-04-11T22:45:39Z,com.docker.dhi.shell=",
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
    "RunningFor": "Less than a second ago",
    "Service": "mcp-server",
    "Size": "0B",
    "State": "running",
    "Status": "Up Less than a second"
  },
  {
    "Command": "\"python server.py\"",
    "CreatedAt": "2026-04-14 16:52:39 +0100 BST",
    "ExitCode": 0,
    "Health": "",
    "ID": "3cf962fc489f",
    "Image": "apim-simulator-mock-backend:latest",
    "Labels": "com.docker.compose.project.working_dir=.,com.docker.dhi.compliance=cis,com.docker.dhi.package-manager=,com.docker.compose.oneoff=False,com.docker.compose.project=apim-simulator,com.docker.compose.project.config_files=./compose.yml,./compose.private.yml,./compose.mcp.yml,com.docker.dhi.definition=image/python/debian-13/3.13,com.docker.dhi.shell=,com.docker.dhi.variant=runtime,com.docker.dhi.version=3.13.12-debian13,desktop.docker.io/ports.scheme=v2,com.docker.compose.service=mock-backend,com.docker.dhi.date.end-of-life=2029-10-31,com.docker.dhi.distro=debian-13,com.docker.dhi.entitlement=public,com.docker.dhi.flavor=,com.docker.dhi.title=Python 3.13.x,com.docker.dhi.url=https://dhi.io/catalog/python,com.docker.compose.version=5.1.1,com.docker.dhi.chain-id=sha256:e68172a1b009e121980466426bb3c0b7a6184cc9d5a4200b57ee2dc4292779da,com.docker.dhi.created=2026-04-08T03:14:50Z,com.docker.dhi.date.release=2024-10-07,com.docker.dhi.name=dhi/python,com.docker.compose.config-hash=815e10da41b3af6176108902ed23ffc47edfaf9c27a33e160094fa0330e92164,com.docker.compose.container-number=1,com.docker.compose.depends_on=,com.docker.compose.image=sha256:41f1c80a74e650c08c6fafbc550916824926352f5f9a655f97e03d078ebb0f25",
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
    "RunningFor": "Less than a second ago",
    "Service": "mock-backend",
    "Size": "0B",
    "State": "running",
    "Status": "Up Less than a second"
  }
]
```

```bash
set -euo pipefail
smoke_log="$(mktemp)"
make smoke-private >"$smoke_log" 2>&1 || { cat "$smoke_log"; exit 1; }
jq -n \
  --arg smoke_log "$(cat "$smoke_log")" \
  '{
    localhost_8000_reachable: false,
    smoke_private: "passed",
    smoke_output: ($smoke_log | split("\n") | map(select(length > 0)))
  }'
rm -f "$smoke_log"

```

```output
{
  "localhost_8000_reachable": false,
  "smoke_private": "passed",
  "smoke_output": [
    "/Applications/Xcode.app/Contents/Developer/usr/bin/make check-private-port-clear",
    "uv run --project . python -c \"import socket; sock = socket.socket(); sock.settimeout(1); code = sock.connect_ex(('127.0.0.1', 8000)); sock.close(); print('Host port 8000 is unavailable, as required for private mode.') if code else (_ for _ in ()).throw(SystemExit('localhost:8000 is already reachable before private-mode launch; stop the conflicting listener before continuing'))\"",
    "Host port 8000 is unavailable, as required for private mode.",
    "docker compose  -f compose.yml -f compose.private.yml -f compose.mcp.yml run --rm --entrypoint python3 smoke-runner scripts/run_smoke_private.py",
    " Container apim-simulator-smoke-runner-run-0abc9a94a4e8 Creating ",
    " Container apim-simulator-smoke-runner-run-0abc9a94a4e8 Created ",
    "MCP smoke passed",
    "- server: APIM Simulator Demo MCP Server",
    "- tools: add_numbers, uppercase",
    "- add_numbers: {",
    "  \"sum\": 5",
    "}",
    "Private smoke passed",
    "- base_url: http://apim-simulator:8000"
  ]
}
```
