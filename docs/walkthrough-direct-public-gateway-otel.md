# APIM Simulator Walkthrough: Direct Public Gateway With OTEL

Generated from a live run against the local repository.

`make up-otel` adds the LGTM stack on [https://lgtm.apim.127.0.0.1.sslip.io:8443](https://lgtm.apim.127.0.0.1.sslip.io:8443) so APIM traffic is visible in Grafana, Loki, Tempo, and Prometheus.

```bash
set -euo pipefail
make down >/dev/null 2>&1 || true
log="$(mktemp)"
make up-otel >"$log" 2>&1 || { cat "$log"; exit 1; }
for _ in $(seq 1 90); do
  curl -fsS http://localhost:8000/apim/health >/dev/null 2>&1 && curl -fsS "$GRAFANA_BASE_URL/api/health" >/dev/null 2>&1 && break
  sleep 1
done
docker compose -f compose.yml -f compose.public.yml -f compose.otel.yml ps --format json | jq -sS .
rm -f "$log"

```

```output
[
  {
    "Command": "\"/app/.venv/bin/pyth…\"",
    "CreatedAt": "2026-04-15 18:48:16 +0100 BST",
    "ExitCode": 0,
    "Health": "",
    "ID": "11546f84b70b",
    "Image": "apim-simulator:latest",
    "Labels": "com.docker.compose.image=sha256:5b7f91253dd00f21741b7e1ec644d8ea23d3c887598a39238e5916d8e29ed1aa,com.docker.compose.oneoff=False,com.docker.compose.version=5.1.1,com.docker.dhi.chain-id=sha256:b9e7c6e6bf9a389eaa805f1244eea298c7ecd133127518ede00ede39add3df83,com.docker.dhi.date.end-of-life=2029-10-31,com.docker.dhi.date.release=2024-10-07,com.docker.dhi.definition=image/python/debian-13/3.13,com.docker.compose.config-hash=b389fb38dd27453fa599cc64aa7e56ce183c6f4b512195894289acdaf05e9504,com.docker.compose.project.config_files=./compose.yml,./compose.public.yml,./compose.otel.yml,com.docker.compose.service=apim-simulator,com.docker.dhi.compliance=cis,com.docker.dhi.name=dhi/python,com.docker.dhi.shell=,com.docker.dhi.url=https://dhi.io/catalog/python,com.docker.dhi.variant=runtime,com.docker.compose.container-number=1,com.docker.compose.project.working_dir=.,com.docker.dhi.distro=debian-13,com.docker.dhi.entitlement=public,com.docker.dhi.flavor=,com.docker.dhi.version=3.13.13-debian13,com.docker.compose.depends_on=mock-backend:service_started:false,lgtm:service_started:false,com.docker.compose.project=apim-simulator,com.docker.dhi.created=2026-04-11T22:45:39Z,com.docker.dhi.package-manager=,com.docker.dhi.title=Python 3.13.x,desktop.docker.io/ports.scheme=v2,desktop.docker.io/ports/8000/tcp=:8000",
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
    "RunningFor": "8 seconds ago",
    "Service": "apim-simulator",
    "Size": "0B",
    "State": "running",
    "Status": "Up 7 seconds"
  },
  {
    "Command": "\"/otel-lgtm/run-all.…\"",
    "CreatedAt": "2026-04-15 18:48:16 +0100 BST",
    "ExitCode": 0,
    "Health": "starting",
    "ID": "9f35ee9370d3",
    "Image": "grafana/otel-lgtm:0.24.0@sha256:a7fbde2893d86ae4807701bc482736243e584eb90b5faa273d291ffff2a1374f",
    "Labels": "com.docker.compose.version=5.1.1,com.redhat.license_terms=https://www.redhat.com/en/about/red-hat-end-user-license-agreements#UBI,desktop.docker.io/binds/1/Source=./observability/grafana/provisioning/dashboards/apim-simulator.yaml,maintainer=Grafana Labs,org.opencontainers.image.ref.name=v0.24.0,org.opencontainers.image.source=https://github.com/grafana/docker-otel-lgtm,release=,vcs-type=git,com.docker.compose.image=sha256:a7fbde2893d86ae4807701bc482736243e584eb90b5faa273d291ffff2a1374f,com.docker.compose.oneoff=False,desktop.docker.io/binds/1/SourceKind=hostFile,desktop.docker.io/binds/2/Target=/otel-lgtm/custom-dashboards,org.opencontainers.image.description=OpenTelemetry backend in a Docker image,org.opencontainers.image.documentation=https://github.com/grafana/docker-otel-lgtm/blob/main/README.md,org.opencontainers.image.vendor=Grafana Labs,build-date=,com.docker.compose.depends_on=,distribution-scope=public,io.buildah.version=,name=grafana/otel-lgtm,vcs-ref=7ac6a7cf03434cc414b5f1228c63b52069cf0f65,com.docker.compose.project.working_dir=.,desktop.docker.io/binds/2/Source=./observability/grafana/dashboards,desktop.docker.io/binds/2/SourceKind=hostFile,io.k8s.display-name=Grafana LGTM,io.openshift.expose-services=,org.opencontainers.image.authors=Grafana Labs,org.opencontainers.image.revision=7ac6a7cf03434cc414b5f1228c63b52069cf0f65,org.opencontainers.image.title=docker-otel-lgtm,com.docker.compose.project=apim-simulator,cpe=,desktop.docker.io/ports/4317/tcp=:4317,desktop.docker.io/ports/4318/tcp=:4318,org.opencontainers.image.licenses=Apache-2.0,summary=An OpenTelemetry backend in a Docker image,com.docker.compose.config-hash=9759eb97aaa5b2755de94820d28d5695a2b75ee9b22bde59eac0493dcf585f1c,com.redhat.component=ubi9-micro-container,desktop.docker.io/binds/1/Target=/otel-lgtm/grafana/conf/provisioning/dashboards/apim-simulator.yaml,io.k8s.description=An open source backend for OpenTelemetry that's intended for development, demo, and testing environments.,org.opencontainers.image.created=2026-04-10T09:33:00.461Z,org.opencontainers.image.version=0.24.0,com.docker.compose.container-number=1,desktop.docker.io/ports.scheme=v2,url=https://github.com/grafana/docker-otel-lgtm,architecture=aarch64,com.docker.compose.project.config_files=./compose.yml,./compose.public.yml,./compose.otel.yml,com.docker.compose.service=lgtm,description=An open source backend for OpenTelemetry that's intended for development, demo, and testing environments.,org.opencontainers.image.url=https://github.com/grafana/docker-otel-lgtm,vendor=Grafana Labs,version=v0.24.0",
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
    "RunningFor": "8 seconds ago",
    "Service": "lgtm",
    "Size": "0B",
    "State": "running",
    "Status": "Up 7 seconds (health: starting)"
  },
  {
    "Command": "\"nginx -g 'daemon of…\"",
    "CreatedAt": "2026-04-15 18:48:16 +0100 BST",
    "ExitCode": 0,
    "Health": "",
    "ID": "2ade30d9a7bc",
    "Image": "dhi.io/nginx:1.29.5-debian13",
    "Labels": "com.docker.compose.image=sha256:9683af47feae3bab0031b489ed85f93f340a0f8b83a2edccc9f761dbfce1bffd,com.docker.compose.oneoff=False,com.docker.compose.project=apim-simulator,com.docker.dhi.compliance=cis,com.docker.dhi.flavor=,com.docker.dhi.title=Nginx mainline,com.docker.dhi.variant=runtime,desktop.docker.io/binds/1/Target=/etc/nginx/nginx.conf,desktop.docker.io/ports.scheme=v2,com.docker.compose.version=5.1.1,com.docker.dhi.definition=image/nginx/debian-13/mainline,com.docker.dhi.name=dhi/nginx,com.docker.dhi.shell=,com.docker.dhi.version=1.29.5-debian13,com.docker.compose.config-hash=3edba5ad2ac9cfd6f4c060a20619a208e20d5ec2e9a3d2aae1578d1f7b06f423,com.docker.compose.project.config_files=./compose.yml,./compose.public.yml,./compose.otel.yml,desktop.docker.io/binds/0/Target=/etc/nginx/certs,com.docker.compose.container-number=1,com.docker.compose.service=lgtm-proxy,com.docker.dhi.created=2026-02-05T05:17:44Z,com.docker.dhi.url=https://dhi.io/catalog/nginx,desktop.docker.io/binds/0/Source=./examples/edge/certs,desktop.docker.io/binds/1/Source=./observability/lgtm/nginx.conf,desktop.docker.io/binds/1/SourceKind=hostFile,com.docker.dhi.chain-id=sha256:12d6f6bfdcac60cd53148007d8b5ee2c5a827f82ad0a8568232264eb95b6f5e2,com.docker.dhi.distro=debian-13,com.docker.compose.depends_on=lgtm:service_started:false,com.docker.dhi.date.release=2025-06-24,desktop.docker.io/binds/0/SourceKind=hostFile,com.docker.compose.project.working_dir=.,com.docker.dhi.package-manager=,desktop.docker.io/ports/8443/tcp=:8443",
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
    "RunningFor": "8 seconds ago",
    "Service": "lgtm-proxy",
    "Size": "0B",
    "State": "running",
    "Status": "Up 7 seconds"
  },
  {
    "Command": "\"python server.py\"",
    "CreatedAt": "2026-04-15 18:48:16 +0100 BST",
    "ExitCode": 0,
    "Health": "",
    "ID": "c910e8ce877b",
    "Image": "apim-simulator-mock-backend:latest",
    "Labels": "com.docker.dhi.chain-id=sha256:e68172a1b009e121980466426bb3c0b7a6184cc9d5a4200b57ee2dc4292779da,com.docker.dhi.created=2026-04-08T03:14:50Z,com.docker.dhi.entitlement=public,com.docker.dhi.flavor=,com.docker.dhi.shell=,com.docker.dhi.version=3.13.12-debian13,com.docker.compose.container-number=1,com.docker.compose.image=sha256:bc515ad39af9f2ed507bf60cc9b7063361eeba3a84d4bd3475f983d9a707eae5,com.docker.compose.project.config_files=./compose.yml,./compose.public.yml,./compose.otel.yml,com.docker.compose.project.working_dir=.,com.docker.compose.service=mock-backend,com.docker.dhi.date.release=2024-10-07,com.docker.dhi.definition=image/python/debian-13/3.13,com.docker.dhi.url=https://dhi.io/catalog/python,com.docker.compose.project=apim-simulator,com.docker.dhi.date.end-of-life=2029-10-31,com.docker.dhi.title=Python 3.13.x,desktop.docker.io/ports.scheme=v2,com.docker.dhi.compliance=cis,com.docker.compose.depends_on=,com.docker.compose.oneoff=False,com.docker.compose.version=5.1.1,com.docker.dhi.distro=debian-13,com.docker.dhi.name=dhi/python,com.docker.dhi.package-manager=,com.docker.dhi.variant=runtime,com.docker.compose.config-hash=815e10da41b3af6176108902ed23ffc47edfaf9c27a33e160094fa0330e92164",
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
    "RunningFor": "8 seconds ago",
    "Service": "mock-backend",
    "Size": "0B",
    "State": "running",
    "Status": "Up 7 seconds"
  }
]
```

```bash
set -euo pipefail
verify_log="$(mktemp)"
make verify-otel >"$verify_log" 2>&1 || { cat "$verify_log"; exit 1; }
apim_health="$(curl -fsS http://localhost:8000/apim/health)"
grafana_health="$(curl -fsS "$GRAFANA_BASE_URL/api/health")"
jq -n \
  --argjson apim_health "$apim_health" \
  --argjson grafana_health "$grafana_health" \
  --arg verify_log "$(cat "$verify_log")" \
  '{
    apim_health: $apim_health,
    grafana_health: $grafana_health,
    verify_otel: "passed",
    verify_output: ($verify_log | split("\n") | map(select(length > 0)))
  }'
rm -f "$verify_log"

```

```output
{
  "apim_health": {
    "status": "healthy"
  },
  "grafana_health": {
    "database": "ok",
    "version": "12.4.2",
    "commit": "ebade4c739e1aface4ce094934ad85374887a680"
  },
  "verify_otel": "passed",
  "verify_output": [
    "uv run --project . python scripts/verify_otel.py",
    "Grafana healthy: version=12.4.2",
    "APIM metrics visible: 2 series",
    "Loki services: apim-simulator, hello-api",
    "Tempo services: apim-simulator",
    "otel verification passed"
  ]
}
```

```bash {image}
set -euo pipefail
rodney stop >/dev/null 2>&1 || true
rm -f "$HOME/.rodney/chrome-data/SingletonLock"
rodney start >/tmp/rodney-start.log 2>&1 || true
sleep 2
rodney open "$GRAFANA_BASE_URL/d/apim-simulator-overview/apim-simulator-overview" >/dev/null
rodney waitload >/dev/null
rodney waitstable >/dev/null
rodney sleep 2 >/dev/null
rodney screenshot walkthrough-core-grafana.png

```

![b8777299-2026-04-15](b8777299-2026-04-15.png)
