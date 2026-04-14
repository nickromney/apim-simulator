# APIM Simulator Walkthrough: Todo Demo With OTEL

Generated from a live run against the local repository.

`make up-todo-otel` combines the browser-backed todo flow with the LGTM stack so the APIM route tags and todo backend telemetry are both visible.

```bash
set -euo pipefail
make down >/dev/null 2>&1 || true
log="$(mktemp)"
make up-todo-otel >"$log" 2>&1 || { cat "$log"; exit 1; }
ready=false
for _ in $(seq 1 120); do
  if curl -fsS "$TODO_FRONTEND_BASE_URL" 2>/dev/null | rg -q 'Gateway-Proof Todo' \
    && curl -fsS "$GRAFANA_BASE_URL/api/health" >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done
if [[ "$ready" != true ]]; then
  echo "todo OTEL demo did not become ready within 120 seconds" >&2
  docker compose -f compose.todo.yml -f compose.todo.otel.yml ps -a --format json | jq -sS .
  docker compose -f compose.todo.yml -f compose.todo.otel.yml logs --tail 200 todo-frontend apim-simulator todo-api lgtm || true
  exit 1
fi
docker compose -f compose.todo.yml -f compose.todo.otel.yml ps --format json | jq -sS .
rm -f "$log"

```

```output
[
  {
    "Command": "\"/app/.venv/bin/pyth…\"",
    "CreatedAt": "2026-04-14 16:55:56 +0100 BST",
    "ExitCode": 0,
    "Health": "healthy",
    "ID": "5108c3ae034e",
    "Image": "apim-simulator:latest",
    "Labels": "com.docker.dhi.definition=image/python/debian-13/3.13,com.docker.dhi.shell=,com.docker.dhi.title=Python 3.13.x,com.docker.compose.project.config_files=./compose.todo.yml,./compose.todo.otel.yml,com.docker.dhi.compliance=cis,com.docker.dhi.created=2026-04-11T22:45:39Z,com.docker.dhi.package-manager=,com.docker.dhi.url=https://dhi.io/catalog/python,com.docker.dhi.variant=runtime,desktop.docker.io/ports/8000/tcp=:8000,com.docker.compose.image=sha256:84cee832e3f41e228d8bf6824a54094539d4c77dd11b9bb87b02296f899ef277,com.docker.compose.service=apim-simulator,com.docker.compose.version=5.1.1,com.docker.dhi.chain-id=sha256:b9e7c6e6bf9a389eaa805f1244eea298c7ecd133127518ede00ede39add3df83,com.docker.dhi.distro=debian-13,com.docker.dhi.flavor=,com.docker.compose.container-number=1,com.docker.compose.oneoff=False,com.docker.dhi.date.end-of-life=2029-10-31,com.docker.dhi.entitlement=public,com.docker.dhi.name=dhi/python,com.docker.dhi.version=3.13.13-debian13,desktop.docker.io/ports.scheme=v2,com.docker.compose.config-hash=dd229ee3d85482b29953098a94255a6ca31e19476c4e101f7d6832f1b6f5f600,com.docker.compose.depends_on=lgtm:service_started:false,todo-api:service_healthy:false,com.docker.compose.project=apim-simulator-todo,com.docker.compose.project.working_dir=.,com.docker.dhi.date.release=2024-10-07",
    "LocalVolumes": "0",
    "Mounts": "",
    "Name": "apim-simulator-todo-apim-simulator-1",
    "Names": "apim-simulator-todo-apim-simulator-1",
    "Networks": "apim-simulator-todo_todo",
    "Ports": "0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp",
    "Project": "apim-simulator-todo",
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
    "RunningFor": "13 seconds ago",
    "Service": "apim-simulator",
    "Size": "0B",
    "State": "running",
    "Status": "Up 6 seconds (healthy)"
  },
  {
    "Command": "\"/otel-lgtm/run-all.…\"",
    "CreatedAt": "2026-04-14 16:55:55 +0100 BST",
    "ExitCode": 0,
    "Health": "starting",
    "ID": "ef272efa3979",
    "Image": "grafana/otel-lgtm:0.24.0@sha256:a7fbde2893d86ae4807701bc482736243e584eb90b5faa273d291ffff2a1374f",
    "Labels": "com.docker.compose.container-number=1,com.docker.compose.project=apim-simulator-todo,com.docker.compose.service=lgtm,desktop.docker.io/ports/4317/tcp=:4317,distribution-scope=public,org.opencontainers.image.vendor=Grafana Labs,org.opencontainers.image.version=0.24.0,vendor=Grafana Labs,architecture=aarch64,com.redhat.component=ubi9-micro-container,desktop.docker.io/binds/1/Source=./observability/grafana/provisioning/dashboards/apim-simulator.yaml,desktop.docker.io/ports/4318/tcp=:4318,io.k8s.display-name=Grafana LGTM,name=grafana/otel-lgtm,org.opencontainers.image.created=2026-04-10T09:33:00.461Z,version=v0.24.0,com.docker.compose.version=5.1.1,description=An open source backend for OpenTelemetry that's intended for development, demo, and testing environments.,desktop.docker.io/ports.scheme=v2,io.buildah.version=,cpe=,desktop.docker.io/binds/2/SourceKind=hostFile,org.opencontainers.image.revision=7ac6a7cf03434cc414b5f1228c63b52069cf0f65,summary=An OpenTelemetry backend in a Docker image,com.docker.compose.project.config_files=./compose.todo.yml,./compose.todo.otel.yml,desktop.docker.io/binds/2/Target=/otel-lgtm/custom-dashboards,maintainer=Grafana Labs,org.opencontainers.image.description=OpenTelemetry backend in a Docker image,url=https://github.com/grafana/docker-otel-lgtm,com.docker.compose.project.working_dir=.,desktop.docker.io/binds/1/SourceKind=hostFile,desktop.docker.io/binds/1/Target=/otel-lgtm/grafana/conf/provisioning/dashboards/apim-simulator.yaml,desktop.docker.io/binds/2/Source=./observability/grafana/dashboards,io.k8s.description=An open source backend for OpenTelemetry that's intended for development, demo, and testing environments.,org.opencontainers.image.licenses=Apache-2.0,vcs-ref=7ac6a7cf03434cc414b5f1228c63b52069cf0f65,com.docker.compose.config-hash=2b6b2b92e6c03db415414327c985011e76bf1363101d3716bab7762a561b7407,com.docker.compose.image=sha256:a7fbde2893d86ae4807701bc482736243e584eb90b5faa273d291ffff2a1374f,com.docker.compose.oneoff=False,io.openshift.expose-services=,org.opencontainers.image.authors=Grafana Labs,org.opencontainers.image.ref.name=v0.24.0,org.opencontainers.image.source=https://github.com/grafana/docker-otel-lgtm,vcs-type=git,build-date=,com.docker.compose.depends_on=,com.redhat.license_terms=https://www.redhat.com/en/about/red-hat-end-user-license-agreements#UBI,org.opencontainers.image.documentation=https://github.com/grafana/docker-otel-lgtm/blob/main/README.md,org.opencontainers.image.title=docker-otel-lgtm,org.opencontainers.image.url=https://github.com/grafana/docker-otel-lgtm,release=",
    "LocalVolumes": "1",
    "Mounts": "apim-simulator…,/host_mnt/User…,/host_mnt/User…",
    "Name": "apim-simulator-todo-lgtm-1",
    "Names": "apim-simulator-todo-lgtm-1",
    "Networks": "apim-simulator-todo_todo",
    "Ports": "0.0.0.0:4317-4318->4317-4318/tcp, [::]:4317-4318->4317-4318/tcp",
    "Project": "apim-simulator-todo",
    "Publishers": [
      {
        "Protocol": "tcp",
        "PublishedPort": 4317,
        "TargetPort": 4317,
        "URL": "0.0.0.0"
      },
      {
        "Protocol": "tcp",
        "PublishedPort": 4317,
        "TargetPort": 4317,
        "URL": "::"
      },
      {
        "Protocol": "tcp",
        "PublishedPort": 4318,
        "TargetPort": 4318,
        "URL": "0.0.0.0"
      },
      {
        "Protocol": "tcp",
        "PublishedPort": 4318,
        "TargetPort": 4318,
        "URL": "::"
      }
    ],
    "RunningFor": "14 seconds ago",
    "Service": "lgtm",
    "Size": "0B",
    "State": "running",
    "Status": "Up 12 seconds (health: starting)"
  },
  {
    "Command": "\"nginx -g 'daemon of…\"",
    "CreatedAt": "2026-04-14 16:55:55 +0100 BST",
    "ExitCode": 0,
    "Health": "",
    "ID": "58a90789f30b",
    "Image": "dhi.io/nginx:1.29.5-debian13",
    "Labels": "desktop.docker.io/binds/0/Target=/etc/nginx/nginx.conf,desktop.docker.io/binds/1/Target=/etc/nginx/certs,com.docker.compose.service=lgtm-proxy,com.docker.dhi.title=Nginx mainline,desktop.docker.io/ports.scheme=v2,com.docker.compose.project.working_dir=.,com.docker.dhi.variant=runtime,desktop.docker.io/binds/1/Source=./examples/edge/certs,desktop.docker.io/binds/1/SourceKind=hostFile,com.docker.compose.depends_on=lgtm:service_started:false,com.docker.dhi.distro=debian-13,desktop.docker.io/ports/8443/tcp=:8443,com.docker.dhi.chain-id=sha256:12d6f6bfdcac60cd53148007d8b5ee2c5a827f82ad0a8568232264eb95b6f5e2,com.docker.dhi.date.release=2025-06-24,com.docker.dhi.flavor=,com.docker.dhi.url=https://dhi.io/catalog/nginx,com.docker.dhi.version=1.29.5-debian13,com.docker.compose.version=5.1.1,com.docker.dhi.definition=image/nginx/debian-13/mainline,com.docker.dhi.name=dhi/nginx,com.docker.compose.image=sha256:9683af47feae3bab0031b489ed85f93f340a0f8b83a2edccc9f761dbfce1bffd,com.docker.dhi.shell=,desktop.docker.io/binds/0/Source=./observability/lgtm/nginx.conf,desktop.docker.io/binds/0/SourceKind=hostFile,com.docker.compose.container-number=1,com.docker.compose.project=apim-simulator-todo,com.docker.compose.project.config_files=./compose.todo.yml,./compose.todo.otel.yml,com.docker.dhi.compliance=cis,com.docker.dhi.created=2026-02-05T05:17:44Z,com.docker.dhi.package-manager=,com.docker.compose.config-hash=fb287947285274b3cc4cab68c420ef3d7586dd5c67e65ff31888bc69e4a41f75,com.docker.compose.oneoff=False",
    "LocalVolumes": "0",
    "Mounts": "/host_mnt/User…,/host_mnt/User…",
    "Name": "apim-simulator-todo-lgtm-proxy-1",
    "Names": "apim-simulator-todo-lgtm-proxy-1",
    "Networks": "apim-simulator-todo_todo",
    "Ports": "0.0.0.0:8443->8443/tcp, [::]:8443->8443/tcp",
    "Project": "apim-simulator-todo",
    "Publishers": [
      {
        "Protocol": "tcp",
        "PublishedPort": 8443,
        "TargetPort": 8443,
        "URL": "0.0.0.0"
      },
      {
        "Protocol": "tcp",
        "PublishedPort": 8443,
        "TargetPort": 8443,
        "URL": "::"
      }
    ],
    "RunningFor": "14 seconds ago",
    "Service": "lgtm-proxy",
    "Size": "0B",
    "State": "running",
    "Status": "Up 12 seconds"
  },
  {
    "Command": "\"/app/.venv/bin/uvic…\"",
    "CreatedAt": "2026-04-14 16:55:55 +0100 BST",
    "ExitCode": 0,
    "Health": "healthy",
    "ID": "99eb3783f04b",
    "Image": "apim-simulator-todo-api:latest",
    "Labels": "com.docker.dhi.url=https://dhi.io/catalog/python,desktop.docker.io/ports.scheme=v2,com.docker.compose.container-number=1,com.docker.compose.project=apim-simulator-todo,com.docker.dhi.chain-id=sha256:b9e7c6e6bf9a389eaa805f1244eea298c7ecd133127518ede00ede39add3df83,com.docker.dhi.date.release=2024-10-07,com.docker.dhi.shell=,com.docker.dhi.variant=runtime,com.docker.dhi.version=3.13.13-debian13,com.docker.compose.depends_on=lgtm:service_started:false,com.docker.compose.image=sha256:1a2ac8bf2629d4544d38a9a4f89f8683c1b8a54e6a8f0e9612bf48c1b9f69acc,com.docker.compose.oneoff=False,com.docker.dhi.compliance=cis,com.docker.compose.project.config_files=./compose.todo.yml,./compose.todo.otel.yml,com.docker.compose.project.working_dir=.,com.docker.compose.service=todo-api,com.docker.compose.version=5.1.1,com.docker.dhi.date.end-of-life=2029-10-31,com.docker.dhi.distro=debian-13,com.docker.dhi.entitlement=public,com.docker.dhi.flavor=,com.docker.compose.config-hash=c0668f4ea44c78a4e5970c2ce1e95bc8f743b3a9c15eb03df8d027f6b3020bf9,com.docker.dhi.created=2026-04-11T22:45:39Z,com.docker.dhi.definition=image/python/debian-13/3.13,com.docker.dhi.name=dhi/python,com.docker.dhi.package-manager=,com.docker.dhi.title=Python 3.13.x",
    "LocalVolumes": "0",
    "Mounts": "",
    "Name": "apim-simulator-todo-todo-api-1",
    "Names": "apim-simulator-todo-todo-api-1",
    "Networks": "apim-simulator-todo_todo",
    "Ports": "8000/tcp",
    "Project": "apim-simulator-todo",
    "Publishers": [
      {
        "Protocol": "tcp",
        "PublishedPort": 0,
        "TargetPort": 8000,
        "URL": ""
      }
    ],
    "RunningFor": "14 seconds ago",
    "Service": "todo-api",
    "Size": "0B",
    "State": "running",
    "Status": "Up 12 seconds (healthy)"
  },
  {
    "Command": "\"/usr/local/bin/runt…\"",
    "CreatedAt": "2026-04-14 16:55:56 +0100 BST",
    "ExitCode": 0,
    "Health": "",
    "ID": "319b2abfb45c",
    "Image": "apim-simulator-todo-frontend:latest",
    "Labels": "com.docker.dhi.chain-id=sha256:12d6f6bfdcac60cd53148007d8b5ee2c5a827f82ad0a8568232264eb95b6f5e2,com.docker.dhi.created=2026-02-05T05:17:44Z,com.docker.dhi.flavor=,com.docker.dhi.title=Nginx mainline,com.docker.dhi.variant=runtime,desktop.docker.io/ports/8080/tcp=:3000,com.docker.compose.project.config_files=./compose.todo.yml,./compose.todo.otel.yml,com.docker.compose.project.working_dir=.,com.docker.compose.service=todo-frontend,com.docker.dhi.compliance=cis,com.docker.dhi.version=1.29.5-debian13,desktop.docker.io/ports.scheme=v2,com.docker.compose.container-number=1,com.docker.compose.image=sha256:93d34939153278d9a4829369b799d20555db5fc605f80e4019c6d39f9db5f0da,com.docker.compose.oneoff=False,com.docker.compose.project=apim-simulator-todo,com.docker.compose.version=5.1.1,com.docker.dhi.definition=image/nginx/debian-13/mainline,com.docker.compose.config-hash=251d40f01b2b1ff5eed6af043affaf5238d060b64066675bb5cb4bf6edc47e53,com.docker.compose.depends_on=apim-simulator:service_healthy:false,com.docker.dhi.date.release=2025-06-24,com.docker.dhi.distro=debian-13,com.docker.dhi.name=dhi/nginx,com.docker.dhi.package-manager=,com.docker.dhi.shell=,com.docker.dhi.url=https://dhi.io/catalog/nginx",
    "LocalVolumes": "0",
    "Mounts": "",
    "Name": "apim-simulator-todo-todo-frontend-1",
    "Names": "apim-simulator-todo-todo-frontend-1",
    "Networks": "apim-simulator-todo_todo",
    "Ports": "0.0.0.0:3000->8080/tcp, [::]:3000->8080/tcp",
    "Project": "apim-simulator-todo",
    "Publishers": [
      {
        "Protocol": "tcp",
        "PublishedPort": 3000,
        "TargetPort": 8080,
        "URL": "0.0.0.0"
      },
      {
        "Protocol": "tcp",
        "PublishedPort": 3000,
        "TargetPort": 8080,
        "URL": "::"
      }
    ],
    "RunningFor": "13 seconds ago",
    "Service": "todo-frontend",
    "Size": "0B",
    "State": "running",
    "Status": "Up Less than a second"
  }
]
```

```bash
set -euo pipefail
verify_log="$(mktemp)"
make verify-todo-otel >"$verify_log" 2>&1 || { cat "$verify_log"; exit 1; }
grafana_health="$(curl -fsS "$GRAFANA_BASE_URL/api/health")"
jq -n \
  --argjson grafana_health "$grafana_health" \
  --arg verify_log "$(cat "$verify_log")" \
  '{
    grafana_health: $grafana_health,
    verify_todo_otel: "passed",
    verify_output: ($verify_log | split("\n") | map(select(length > 0)))
  }'
rm -f "$verify_log"

```

```output
{
  "grafana_health": {
    "database": "ok",
    "version": "12.4.2",
    "commit": "ebade4c739e1aface4ce094934ad85374887a680"
  },
  "verify_todo_otel": "passed",
  "verify_output": [
    "VERIFY_OTEL_TODO=true uv run --project . python scripts/verify_otel.py",
    "Grafana healthy: version=12.4.2",
    "APIM metrics visible: 6 series",
    "Loki services: apim-simulator, todo-api",
    "Tempo services: apim-simulator, todo-api",
    "Todo metrics visible: 4 series",
    "Tempo APIM route tags: Todo API:Create Todo, Todo API:Health, Todo API:List Todos, Todo API:Update Todo",
    "otel verification passed"
  ]
}
```
