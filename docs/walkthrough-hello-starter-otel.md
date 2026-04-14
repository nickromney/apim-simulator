# APIM Simulator Walkthrough: Hello Starter With OTEL

Generated from a live run against the local repository.

`make up-hello-otel` adds the same LGTM stack used by the core OTEL walkthrough, but this time the hello backend emits its own logs, metrics, and traces too.

```bash
set -euo pipefail
make down >/dev/null 2>&1 || true
log="$(mktemp)"
make up-hello-otel >"$log" 2>&1 || { cat "$log"; exit 1; }
for _ in $(seq 1 90); do
  curl -fsS http://localhost:8000/api/health >/dev/null 2>&1 && curl -fsS "$GRAFANA_BASE_URL/api/health" >/dev/null 2>&1 && break
  sleep 1
done
docker compose -f compose.yml -f compose.public.yml -f compose.hello.yml -f compose.otel.yml -f compose.hello.otel.yml ps --format json | jq -sS .
rm -f "$log"

```

```output
[
  {
    "Command": "\"/app/.venv/bin/pyth…\"",
    "CreatedAt": "2026-04-14 16:54:43 +0100 BST",
    "ExitCode": 0,
    "Health": "",
    "ID": "01e8b75a1ea7",
    "Image": "apim-simulator:latest",
    "Labels": "com.docker.dhi.definition=image/python/debian-13/3.13,com.docker.dhi.entitlement=public,com.docker.dhi.title=Python 3.13.x,desktop.docker.io/ports.scheme=v2,desktop.docker.io/ports/8000/tcp=:8000,com.docker.compose.config-hash=90a4add8d085e4442f52b9897b8b2c66640dad4aa27189b11a8b366edde7f022,com.docker.compose.oneoff=False,com.docker.dhi.chain-id=sha256:b9e7c6e6bf9a389eaa805f1244eea298c7ecd133127518ede00ede39add3df83,com.docker.dhi.date.end-of-life=2029-10-31,com.docker.dhi.shell=,com.docker.dhi.url=https://dhi.io/catalog/python,com.docker.dhi.variant=runtime,com.docker.compose.container-number=1,com.docker.compose.project=apim-simulator,com.docker.dhi.compliance=cis,com.docker.dhi.created=2026-04-11T22:45:39Z,com.docker.dhi.name=dhi/python,com.docker.compose.depends_on=lgtm:service_started:false,mock-backend:service_started:false,hello-api:service_healthy:false,com.docker.compose.project.config_files=./compose.yml,./compose.public.yml,./compose.hello.yml,./compose.otel.yml,./compose.hello.otel.yml,com.docker.compose.service=apim-simulator,com.docker.dhi.date.release=2024-10-07,com.docker.dhi.distro=debian-13,com.docker.dhi.flavor=,com.docker.dhi.package-manager=,com.docker.dhi.version=3.13.13-debian13,com.docker.compose.image=sha256:129d1f11f5bfba6304ba7323914f8e6718a1848b506a30f9501d64347f9dc565,com.docker.compose.project.working_dir=.,com.docker.compose.version=5.1.1",
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
    "CreatedAt": "2026-04-14 16:54:43 +0100 BST",
    "ExitCode": 0,
    "Health": "healthy",
    "ID": "5bc294bcab06",
    "Image": "apim-simulator-hello-api:latest",
    "Labels": "com.docker.compose.depends_on=lgtm:service_started:false,com.docker.compose.project=apim-simulator,com.docker.dhi.compliance=cis,com.docker.dhi.created=2026-04-11T22:45:39Z,com.docker.dhi.date.release=2024-10-07,com.docker.dhi.definition=image/python/debian-13/3.13,com.docker.dhi.name=dhi/python,com.docker.dhi.url=https://dhi.io/catalog/python,com.docker.compose.config-hash=c1d5e4a868757e2096ca450e9d0fb54da63358552922cc688def7155790e0d33,com.docker.compose.project.config_files=./compose.yml,./compose.public.yml,./compose.hello.yml,./compose.otel.yml,./compose.hello.otel.yml,com.docker.compose.service=hello-api,com.docker.dhi.date.end-of-life=2029-10-31,com.docker.dhi.flavor=,com.docker.dhi.shell=,com.docker.dhi.title=Python 3.13.x,com.docker.dhi.version=3.13.13-debian13,com.docker.compose.container-number=1,com.docker.compose.oneoff=False,com.docker.compose.project.working_dir=.,com.docker.dhi.chain-id=sha256:b9e7c6e6bf9a389eaa805f1244eea298c7ecd133127518ede00ede39add3df83,com.docker.dhi.distro=debian-13,com.docker.dhi.entitlement=public,com.docker.dhi.package-manager=,com.docker.dhi.variant=runtime,com.docker.compose.image=sha256:af422e2215090c205ee7e47202b32f5e2490c3561352299fc4e2fb5e60f1061d,com.docker.compose.version=5.1.1,desktop.docker.io/ports.scheme=v2",
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
    "Command": "\"/otel-lgtm/run-all.…\"",
    "CreatedAt": "2026-04-14 16:54:43 +0100 BST",
    "ExitCode": 0,
    "Health": "starting",
    "ID": "62e054e2ee42",
    "Image": "grafana/otel-lgtm:0.24.0@sha256:a7fbde2893d86ae4807701bc482736243e584eb90b5faa273d291ffff2a1374f",
    "Labels": "org.opencontainers.image.source=https://github.com/grafana/docker-otel-lgtm,version=v0.24.0,build-date=,com.docker.compose.container-number=1,desktop.docker.io/binds/1/Source=./observability/grafana/provisioning/dashboards/apim-simulator.yaml,io.k8s.display-name=Grafana LGTM,org.opencontainers.image.version=0.24.0,release=,com.docker.compose.depends_on=,cpe=,desktop.docker.io/binds/2/Source=./observability/grafana/dashboards,summary=An OpenTelemetry backend in a Docker image,com.docker.compose.image=sha256:a7fbde2893d86ae4807701bc482736243e584eb90b5faa273d291ffff2a1374f,com.docker.compose.oneoff=False,com.docker.compose.project.config_files=./compose.yml,./compose.public.yml,./compose.hello.yml,./compose.otel.yml,./compose.hello.otel.yml,com.redhat.license_terms=https://www.redhat.com/en/about/red-hat-end-user-license-agreements#UBI,desktop.docker.io/ports.scheme=v2,io.buildah.version=,io.k8s.description=An open source backend for OpenTelemetry that's intended for development, demo, and testing environments.,maintainer=Grafana Labs,architecture=aarch64,com.docker.compose.config-hash=9759eb97aaa5b2755de94820d28d5695a2b75ee9b22bde59eac0493dcf585f1c,name=grafana/otel-lgtm,org.opencontainers.image.authors=Grafana Labs,org.opencontainers.image.created=2026-04-10T09:33:00.461Z,org.opencontainers.image.description=OpenTelemetry backend in a Docker image,org.opencontainers.image.documentation=https://github.com/grafana/docker-otel-lgtm/blob/main/README.md,org.opencontainers.image.revision=7ac6a7cf03434cc414b5f1228c63b52069cf0f65,com.redhat.component=ubi9-micro-container,desktop.docker.io/binds/1/SourceKind=hostFile,io.openshift.expose-services=,org.opencontainers.image.url=https://github.com/grafana/docker-otel-lgtm,org.opencontainers.image.vendor=Grafana Labs,vendor=Grafana Labs,com.docker.compose.project.working_dir=.,desktop.docker.io/ports/4317/tcp=:4317,org.opencontainers.image.title=docker-otel-lgtm,vcs-ref=7ac6a7cf03434cc414b5f1228c63b52069cf0f65,vcs-type=git,com.docker.compose.service=lgtm,com.docker.compose.version=5.1.1,desktop.docker.io/binds/2/SourceKind=hostFile,desktop.docker.io/binds/2/Target=/otel-lgtm/custom-dashboards,desktop.docker.io/ports/4318/tcp=:4318,distribution-scope=public,org.opencontainers.image.ref.name=v0.24.0,url=https://github.com/grafana/docker-otel-lgtm,com.docker.compose.project=apim-simulator,description=An open source backend for OpenTelemetry that's intended for development, demo, and testing environments.,desktop.docker.io/binds/1/Target=/otel-lgtm/grafana/conf/provisioning/dashboards/apim-simulator.yaml,org.opencontainers.image.licenses=Apache-2.0",
    "LocalVolumes": "1",
    "Mounts": "apim-simulator…,/host_mnt/User…,/host_mnt/User…",
    "Name": "apim-simulator-lgtm-1",
    "Names": "apim-simulator-lgtm-1",
    "Networks": "apim-simulator_apim",
    "Ports": "0.0.0.0:4317-4318->4317-4318/tcp, [::]:4317-4318->4317-4318/tcp",
    "Project": "apim-simulator",
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
    "RunningFor": "9 seconds ago",
    "Service": "lgtm",
    "Size": "0B",
    "State": "running",
    "Status": "Up 8 seconds (health: starting)"
  },
  {
    "Command": "\"nginx -g 'daemon of…\"",
    "CreatedAt": "2026-04-14 16:54:43 +0100 BST",
    "ExitCode": 0,
    "Health": "",
    "ID": "9e826b2db303",
    "Image": "dhi.io/nginx:1.29.5-debian13",
    "Labels": "desktop.docker.io/binds/0/SourceKind=hostFile,desktop.docker.io/binds/1/SourceKind=hostFile,desktop.docker.io/binds/1/Target=/etc/nginx/certs,com.docker.compose.config-hash=3edba5ad2ac9cfd6f4c060a20619a208e20d5ec2e9a3d2aae1578d1f7b06f423,com.docker.compose.project=apim-simulator,com.docker.compose.service=lgtm-proxy,com.docker.dhi.created=2026-02-05T05:17:44Z,com.docker.dhi.name=dhi/nginx,com.docker.dhi.url=https://dhi.io/catalog/nginx,com.docker.dhi.variant=runtime,com.docker.dhi.version=1.29.5-debian13,com.docker.dhi.date.release=2025-06-24,desktop.docker.io/binds/1/Source=./examples/edge/certs,desktop.docker.io/ports.scheme=v2,desktop.docker.io/ports/8443/tcp=:8443,com.docker.compose.image=sha256:9683af47feae3bab0031b489ed85f93f340a0f8b83a2edccc9f761dbfce1bffd,com.docker.dhi.chain-id=sha256:12d6f6bfdcac60cd53148007d8b5ee2c5a827f82ad0a8568232264eb95b6f5e2,com.docker.dhi.distro=debian-13,com.docker.dhi.title=Nginx mainline,com.docker.dhi.flavor=,com.docker.dhi.shell=,desktop.docker.io/binds/0/Source=./observability/lgtm/nginx.conf,desktop.docker.io/binds/0/Target=/etc/nginx/nginx.conf,com.docker.compose.depends_on=lgtm:service_started:false,com.docker.compose.project.config_files=./compose.yml,./compose.public.yml,./compose.hello.yml,./compose.otel.yml,./compose.hello.otel.yml,com.docker.compose.project.working_dir=.,com.docker.compose.version=5.1.1,com.docker.dhi.compliance=cis,com.docker.dhi.definition=image/nginx/debian-13/mainline,com.docker.compose.oneoff=False,com.docker.dhi.package-manager=,com.docker.compose.container-number=1",
    "LocalVolumes": "0",
    "Mounts": "/host_mnt/User…,/host_mnt/User…",
    "Name": "apim-simulator-lgtm-proxy-1",
    "Names": "apim-simulator-lgtm-proxy-1",
    "Networks": "apim-simulator_apim",
    "Ports": "0.0.0.0:8443->8443/tcp, [::]:8443->8443/tcp",
    "Project": "apim-simulator",
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
    "RunningFor": "9 seconds ago",
    "Service": "lgtm-proxy",
    "Size": "0B",
    "State": "running",
    "Status": "Up 8 seconds"
  },
  {
    "Command": "\"python server.py\"",
    "CreatedAt": "2026-04-14 16:54:43 +0100 BST",
    "ExitCode": 0,
    "Health": "",
    "ID": "d16a10c4491d",
    "Image": "apim-simulator-mock-backend:latest",
    "Labels": "com.docker.compose.service=mock-backend,com.docker.compose.version=5.1.1,com.docker.dhi.chain-id=sha256:e68172a1b009e121980466426bb3c0b7a6184cc9d5a4200b57ee2dc4292779da,com.docker.dhi.distro=debian-13,com.docker.compose.container-number=1,com.docker.compose.depends_on=,com.docker.compose.image=sha256:b9e7b4716e1e4cefea2f6045c4950f192124ecb7448734521070822ef110ace5,com.docker.dhi.entitlement=public,com.docker.dhi.url=https://dhi.io/catalog/python,desktop.docker.io/ports.scheme=v2,com.docker.compose.project.config_files=./compose.yml,./compose.public.yml,./compose.hello.yml,./compose.otel.yml,./compose.hello.otel.yml,com.docker.dhi.date.end-of-life=2029-10-31,com.docker.dhi.flavor=,com.docker.dhi.shell=,com.docker.dhi.variant=runtime,com.docker.dhi.version=3.13.12-debian13,com.docker.compose.project=apim-simulator,com.docker.dhi.compliance=cis,com.docker.dhi.created=2026-04-08T03:14:50Z,com.docker.dhi.date.release=2024-10-07,com.docker.dhi.definition=image/python/debian-13/3.13,com.docker.dhi.name=dhi/python,com.docker.dhi.package-manager=,com.docker.dhi.title=Python 3.13.x,com.docker.compose.config-hash=815e10da41b3af6176108902ed23ffc47edfaf9c27a33e160094fa0330e92164,com.docker.compose.oneoff=False,com.docker.compose.project.working_dir=.",
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
verify_log="$(mktemp)"
make verify-hello-otel >"$verify_log" 2>&1 || { cat "$verify_log"; exit 1; }
grafana_health="$(curl -fsS "$GRAFANA_BASE_URL/api/health")"
jq -n \
  --argjson grafana_health "$grafana_health" \
  --arg verify_log "$(cat "$verify_log")" \
  '{
    grafana_health: $grafana_health,
    verify_hello_otel: "passed",
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
  "verify_hello_otel": "passed",
  "verify_output": [
    "uv run --project . python scripts/verify_hello_otel.py",
    "Grafana healthy: version=12.4.2",
    "APIM metrics visible: 2 series",
    "hello-api metrics visible: 1 series",
    "Loki services: apim-simulator, hello-api",
    "hello-api log sample: health checked",
    "Tempo services: apim-simulator, hello-api",
    "Tempo APIM route tags: Hello API:Health, Hello API:Hello",
    "hello otel verification passed"
  ]
}
```
