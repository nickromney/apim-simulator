# APIM Simulator Walkthrough: Hello Starter With OIDC

Generated from a live run against the local repository.

`make up-hello-oidc` keeps the hello backend but fronts it with Keycloak-backed bearer token validation.

```bash
set -euo pipefail
make down >/dev/null 2>&1 || true
log="$(mktemp)"
make up-hello-oidc >"$log" 2>&1 || { cat "$log"; exit 1; }
for _ in $(seq 1 90); do
  curl -fsS http://localhost:8000/apim/health >/dev/null 2>&1 && curl -fsS http://localhost:8180/realms/subnet-calculator/.well-known/openid-configuration >/dev/null 2>&1 && break
  sleep 1
done
docker compose -f compose.yml -f compose.public.yml -f compose.oidc.yml -f compose.hello.yml ps --format json | jq -sS .
rm -f "$log"

```

```output
[
  {
    "Command": "\"/app/.venv/bin/pyth…\"",
    "CreatedAt": "2026-04-14 16:53:38 +0100 BST",
    "ExitCode": 0,
    "Health": "",
    "ID": "b4671f5272b4",
    "Image": "apim-simulator:latest",
    "Labels": "com.docker.dhi.version=3.13.13-debian13,desktop.docker.io/ports/8000/tcp=:8000,com.docker.compose.depends_on=keycloak:service_healthy:false,hello-api:service_healthy:false,mock-backend:service_started:false,com.docker.compose.image=sha256:1e0c3ae30cd3911904a83b06827268ec9dd9d6055b5dc54463a63df4bb8f7a7c,com.docker.compose.project=apim-simulator,com.docker.compose.project.working_dir=.,com.docker.dhi.compliance=cis,desktop.docker.io/ports.scheme=v2,com.docker.compose.config-hash=c0a37452f793404171aa254ac00aa12e2526a5ded88f1188a0641484ec32569a,com.docker.compose.oneoff=False,com.docker.compose.service=apim-simulator,com.docker.dhi.created=2026-04-11T22:45:39Z,com.docker.dhi.flavor=,com.docker.dhi.name=dhi/python,com.docker.dhi.package-manager=,com.docker.dhi.shell=,com.docker.dhi.date.end-of-life=2029-10-31,com.docker.compose.container-number=1,com.docker.compose.project.config_files=./compose.yml,./compose.public.yml,./compose.oidc.yml,./compose.hello.yml,com.docker.dhi.chain-id=sha256:b9e7c6e6bf9a389eaa805f1244eea298c7ecd133127518ede00ede39add3df83,com.docker.dhi.date.release=2024-10-07,com.docker.dhi.distro=debian-13,com.docker.dhi.entitlement=public,com.docker.dhi.url=https://dhi.io/catalog/python,com.docker.compose.version=5.1.1,com.docker.dhi.definition=image/python/debian-13/3.13,com.docker.dhi.title=Python 3.13.x,com.docker.dhi.variant=runtime",
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
    "RunningFor": "24 seconds ago",
    "Service": "apim-simulator",
    "Size": "0B",
    "State": "running",
    "Status": "Up 2 seconds"
  },
  {
    "Command": "\"/app/.venv/bin/uvic…\"",
    "CreatedAt": "2026-04-14 16:53:38 +0100 BST",
    "ExitCode": 0,
    "Health": "healthy",
    "ID": "84b1f7742a46",
    "Image": "apim-simulator-hello-api:latest",
    "Labels": "com.docker.compose.project.working_dir=.,com.docker.compose.version=5.1.1,com.docker.dhi.date.release=2024-10-07,com.docker.dhi.entitlement=public,com.docker.dhi.name=dhi/python,com.docker.compose.container-number=1,com.docker.compose.image=sha256:2865d311d9a636da317131a1b53447c524eeb2bfa5135c5638add982930df833,com.docker.compose.project.config_files=./compose.yml,./compose.public.yml,./compose.oidc.yml,./compose.hello.yml,com.docker.dhi.compliance=cis,com.docker.dhi.shell=,com.docker.dhi.variant=runtime,com.docker.dhi.version=3.13.13-debian13,desktop.docker.io/ports.scheme=v2,com.docker.compose.depends_on=,com.docker.dhi.created=2026-04-11T22:45:39Z,com.docker.dhi.package-manager=,com.docker.dhi.url=https://dhi.io/catalog/python,com.docker.compose.service=hello-api,com.docker.dhi.chain-id=sha256:b9e7c6e6bf9a389eaa805f1244eea298c7ecd133127518ede00ede39add3df83,com.docker.dhi.date.end-of-life=2029-10-31,com.docker.dhi.definition=image/python/debian-13/3.13,com.docker.dhi.distro=debian-13,com.docker.dhi.flavor=,com.docker.dhi.title=Python 3.13.x,com.docker.compose.config-hash=67f3fb3e730931d9496f989084f451727082e3f09840e8df8a4194d43f2b4014,com.docker.compose.oneoff=False,com.docker.compose.project=apim-simulator",
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
    "RunningFor": "24 seconds ago",
    "Service": "hello-api",
    "Size": "0B",
    "State": "running",
    "Status": "Up 23 seconds (healthy)"
  },
  {
    "Command": "\"/opt/keycloak/bin/k…\"",
    "CreatedAt": "2026-04-14 16:53:38 +0100 BST",
    "ExitCode": 0,
    "Health": "healthy",
    "ID": "502e5fd6de8e",
    "Image": "quay.io/keycloak/keycloak:26.4.7",
    "Labels": "com.docker.compose.project.config_files=./compose.yml,./compose.public.yml,./compose.oidc.yml,./compose.hello.yml,com.docker.compose.version=5.1.1,io.k8s.description=Keycloak Server Image,io.openshift.tags=keycloak security identity,maintainer=https://www.keycloak.org/,org.opencontainers.image.licenses=Apache-2.0,com.docker.compose.project.working_dir=.,com.docker.compose.service=keycloak,com.redhat.license_terms=,cpe=cpe:/a:redhat:enterprise_linux:9::appstream,org.opencontainers.image.documentation=https://www.keycloak.org/documentation,com.docker.compose.depends_on=,description=Keycloak Server Image,name=keycloak,vcs-ref=,version=26.4.7,com.docker.compose.container-number=1,com.docker.compose.oneoff=False,desktop.docker.io/binds/0/Target=/opt/keycloak/data/import/realm-export.json,io.k8s.display-name=Keycloak Server,org.opencontainers.image.description=,summary=Keycloak Server Image,url=https://www.keycloak.org/,vendor=https://www.keycloak.org/,com.docker.compose.image=sha256:9409c59bdfb65dbffa20b11e6f18b8abb9281d480c7ca402f51ed3d5977e6007,com.redhat.build-host=,desktop.docker.io/ports.scheme=v2,distribution-scope=public,io.buildah.version=1.41.4,org.opencontainers.image.created=2025-12-01T08:14:24.495Z,org.opencontainers.image.url=https://github.com/keycloak-rel/keycloak-rel,release=,build-date=2025-11-12T17:00:10Z,com.redhat.component=,io.openshift.expose-services=,vcs-type=git,architecture=aarch64,com.docker.compose.project=apim-simulator,desktop.docker.io/binds/0/SourceKind=hostFile,desktop.docker.io/ports/8080/tcp=:8180,org.opencontainers.image.revision=aa3baec457ee0cdfdff6de1ce256319180a76ee6,org.opencontainers.image.source=https://github.com/keycloak-rel/keycloak-rel,org.opencontainers.image.title=keycloak-rel,com.docker.compose.config-hash=ef29ccb00803353c0b6c2627c8f8c5c5b4c07338465efad85e1e1df4aad6ff6c,desktop.docker.io/binds/0/Source=./examples/subnet-calculator/keycloak/realm-export.json,org.opencontainers.image.version=26.4.7",
    "LocalVolumes": "1",
    "Mounts": "apim-simulator…,/host_mnt/User…",
    "Name": "apim-simulator-keycloak-1",
    "Names": "apim-simulator-keycloak-1",
    "Networks": "apim-simulator_apim",
    "Ports": "0.0.0.0:8180->8080/tcp, [::]:8180->8080/tcp",
    "Project": "apim-simulator",
    "Publishers": [
      {
        "Protocol": "tcp",
        "PublishedPort": 8180,
        "TargetPort": 8080,
        "URL": "0.0.0.0"
      },
      {
        "Protocol": "tcp",
        "PublishedPort": 8180,
        "TargetPort": 8080,
        "URL": "::"
      }
    ],
    "RunningFor": "24 seconds ago",
    "Service": "keycloak",
    "Size": "0B",
    "State": "running",
    "Status": "Up 23 seconds (healthy)"
  },
  {
    "Command": "\"python server.py\"",
    "CreatedAt": "2026-04-14 16:53:38 +0100 BST",
    "ExitCode": 0,
    "Health": "",
    "ID": "23ba61a96df4",
    "Image": "apim-simulator-mock-backend:latest",
    "Labels": "com.docker.compose.project.config_files=./compose.yml,./compose.public.yml,./compose.oidc.yml,./compose.hello.yml,com.docker.compose.service=mock-backend,com.docker.dhi.entitlement=public,com.docker.dhi.package-manager=,com.docker.dhi.title=Python 3.13.x,com.docker.dhi.version=3.13.12-debian13,com.docker.compose.depends_on=,com.docker.compose.project.working_dir=.,com.docker.dhi.created=2026-04-08T03:14:50Z,com.docker.dhi.shell=,desktop.docker.io/ports.scheme=v2,com.docker.compose.container-number=1,com.docker.compose.image=sha256:622aa66dea3d0a6f6d7701c0c3e0578112b82f4b649e45df77844ef147719080,com.docker.compose.version=5.1.1,com.docker.dhi.compliance=cis,com.docker.dhi.date.release=2024-10-07,com.docker.dhi.distro=debian-13,com.docker.dhi.flavor=,com.docker.compose.oneoff=False,com.docker.dhi.chain-id=sha256:e68172a1b009e121980466426bb3c0b7a6184cc9d5a4200b57ee2dc4292779da,com.docker.dhi.date.end-of-life=2029-10-31,com.docker.dhi.definition=image/python/debian-13/3.13,com.docker.dhi.name=dhi/python,com.docker.dhi.url=https://dhi.io/catalog/python,com.docker.dhi.variant=runtime,com.docker.compose.config-hash=815e10da41b3af6176108902ed23ffc47edfaf9c27a33e160094fa0330e92164,com.docker.compose.project=apim-simulator",
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
    "RunningFor": "24 seconds ago",
    "Service": "mock-backend",
    "Size": "0B",
    "State": "running",
    "Status": "Up 23 seconds"
  }
]
```

```bash
set -euo pipefail
smoke_log="$(mktemp)"
SMOKE_HELLO_MODE=oidc-jwt make smoke-hello >"$smoke_log" 2>&1 || { cat "$smoke_log"; exit 1; }
well_known="$(curl -fsS http://localhost:8180/realms/subnet-calculator/.well-known/openid-configuration)"
jq -n \
  --argjson well_known "$well_known" \
  --arg smoke_log "$(cat "$smoke_log")" \
  '{
    oidc: {
      issuer: $well_known.issuer,
      token_endpoint: $well_known.token_endpoint
    },
    smoke_hello: "passed",
    smoke_output: ($smoke_log | split("\n") | map(select(length > 0)))
  }'
rm -f "$smoke_log"

```

```output
{
  "oidc": {
    "issuer": "http://localhost:8180/realms/subnet-calculator",
    "token_endpoint": "http://localhost:8180/realms/subnet-calculator/protocol/openid-connect/token"
  },
  "smoke_hello": "passed",
  "smoke_output": [
    "uv run --project . python scripts/smoke_hello.py",
    "hello smoke passed",
    "- mode: oidc-jwt",
    "- missing bearer: 401",
    "- valid token: 200"
  ]
}
```
