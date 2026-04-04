# APIM Brief Match Review

*2026-04-04T14:18:29Z*

This document records the live checks I used to judge how closely the current implementation matches the APIM simulator brief.

Assessment criteria:
- APIM-shaped management surface aligned to the main SDK resource families
- beginner-friendly local developer flow
- practical migration path for teams moving from AWS API Gateway
- compatibility with Docker and the platform-style mounted-config deployment model

The checks below use the real Docker Compose stacks and the current workspace state.

```bash
git diff --stat
```

```output
 Dockerfile                                     |   2 +-
 README.md                                      |  25 +-
 app/config.py                                  |  10 +-
 app/main.py                                    | 610 ++++++++++++++++++++++---
 compose.yml                                    |   2 +-
 docs/APIM-STARTER-RECIPE.md                    |  10 +-
 docs/APIM-TEAM-PLAYBOOK.md                     |   6 +-
 docs/APIM-TRAINING-GUIDE.md                    |  46 +-
 docs/AZURE-APIM-TERM-MAP.md                    |  29 +-
 examples/basic.json                            |  24 +-
 examples/hello-api/README.md                   |   8 +-
 examples/hello-api/apim.anonymous.json         |  24 +-
 examples/hello-api/apim.oidc.jwt-only.json     |  31 +-
 examples/hello-api/apim.oidc.subscription.json |  31 +-
 examples/hello-api/apim.subscription.json      |  26 +-
 examples/oidc/keycloak.json                    |  46 +-
 examples/todo-app/apim.json                    |  36 +-
 tests/test_gateway.py                          | 295 +++++++++++-
 tests/test_terraform_import.py                 |   4 +-
 19 files changed, 1083 insertions(+), 182 deletions(-)
```

First, verify the Python test suite and lint state before exercising the containerized stacks.

```bash
uv run --extra dev ruff check .
```

```output
All checks passed!
```

```bash
uv run --extra dev pytest -q
```

```output
============================= test session starts ==============================
platform darwin -- Python 3.14.3, pytest-9.0.2, pluggy-1.6.0
rootdir: /Users/nickromney/Developer/personal/apim-simulator
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.12.1
collected 98 items

tests/test_apim_expr.py .                                                [  1%]
tests/test_gateway.py .................................................. [ 52%]
..............                                                           [ 66%]
tests/test_integration_keycloak.py s                                     [ 67%]
tests/test_policy_golden.py ................                             [ 83%]
tests/test_resource_projection.py ..                                     [ 85%]
tests/test_sample_compat.py .                                            [ 86%]
tests/test_scenarios.py .                                                [ 87%]
tests/test_smoke_oidc.py ..                                              [ 89%]
tests/test_terraform_import.py ......                                    [ 95%]
tests/test_todo_api_example.py ....                                      [100%]

======================== 97 passed, 1 skipped in 3.25s =========================
```

Next, prove the default direct-public Docker Compose stack boots and exposes both gateway traffic and the new APIM-style management resource surface.

```bash
set -euo pipefail
make down >/dev/null
make up >/dev/null
printf '\n## compose ps\n'
docker compose -f compose.yml -f compose.public.yml ps
printf '\n## startup\n'
curl -i -sS http://localhost:8000/apim/startup
printf '\n## gateway echo\n'
curl -i -sS http://localhost:8000/api/echo
printf '\n## management apis\n'
curl -i -sS -H 'X-Apim-Tenant-Key: local-dev-tenant-key' http://localhost:8000/apim/management/apis

```

```output
 Container apim-simulator-apim-simulator-1 Stopping 
 Container apim-simulator-apim-simulator-1 Stopped 
 Container apim-simulator-apim-simulator-1 Removing 
 Container apim-simulator-apim-simulator-1 Removed 
 Container apim-simulator-mock-backend-1 Stopping 
 Container apim-simulator-mock-backend-1 Stopped 
 Container apim-simulator-mock-backend-1 Removing 
 Container apim-simulator-mock-backend-1 Removed 
 Network apim-simulator_apim Removing 
 Network apim-simulator_apim Removed 
 Image apim-simulator-mock-backend:latest Building 
 Image apim-simulator:latest Building 
 Image apim-simulator:latest Built 
 Image apim-simulator-mock-backend:latest Built 
 Network apim-simulator_apim Creating 
 Network apim-simulator_apim Created 
 Container apim-simulator-mock-backend-1 Creating 
 Container apim-simulator-mock-backend-1 Created 
 Container apim-simulator-apim-simulator-1 Creating 
 Container apim-simulator-apim-simulator-1 Created 
 Container apim-simulator-mock-backend-1 Starting 
 Container apim-simulator-mock-backend-1 Started 
 Container apim-simulator-apim-simulator-1 Starting 
 Container apim-simulator-apim-simulator-1 Started 

## compose ps
NAME                              IMAGE                                COMMAND                  SERVICE          CREATED                  STATUS                  PORTS
apim-simulator-apim-simulator-1   apim-simulator:latest                "sh -c '/app/.venv/b…"   apim-simulator   Less than a second ago   Up Less than a second   0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp
apim-simulator-mock-backend-1     apim-simulator-mock-backend:latest   "python server.py"       mock-backend     Less than a second ago   Up Less than a second   8080/tcp

## startup
curl: (56) Recv failure: Connection reset by peer
```

Then switch the running hello stack to the new "Migrating From AWS API Gateway" starter config and validate the expected auth behavior and the stage-like `/prod/...` API path.

```bash
set -euo pipefail
make down >/dev/null
HELLO_APIM_CONFIG_PATH=/app/examples/migrating-from-aws-api-gateway/apim.http-api.json make up-hello >/dev/null
printf '\n## compose ps\n'
docker compose -f compose.yml -f compose.public.yml -f compose.hello.yml ps
printf '\n## startup\n'
curl -i -sS http://localhost:8000/apim/startup
printf '\n## no subscription key\n'
curl -i -sS http://localhost:8000/prod/health
printf '\n## health with subscription key\n'
curl -i -sS -H 'Ocp-Apim-Subscription-Key: aws-migration-demo-key' http://localhost:8000/prod/health
printf '\n## hello with subscription key\n'
curl -i -sS -H 'Ocp-Apim-Subscription-Key: aws-migration-demo-key' 'http://localhost:8000/prod/hello?name=aws'
printf '\n## management service\n'
curl -i -sS -H 'X-Apim-Tenant-Key: local-dev-tenant-key' http://localhost:8000/apim/management/service

```

```output
 Container apim-simulator-apim-simulator-1 Stopping 
 Container apim-simulator-apim-simulator-1 Stopped 
 Container apim-simulator-apim-simulator-1 Removing 
 Container apim-simulator-apim-simulator-1 Removed 
 Container apim-simulator-mock-backend-1 Stopping 
 Container apim-simulator-mock-backend-1 Stopped 
 Container apim-simulator-mock-backend-1 Removing 
 Container apim-simulator-mock-backend-1 Removed 
 Network apim-simulator_apim Removing 
 Network apim-simulator_apim Removed 
 Image apim-simulator:latest Building 
 Image apim-simulator-hello-api:latest Building 
 Image apim-simulator-mock-backend:latest Building 
 Image apim-simulator-hello-api:latest Built 
 Image apim-simulator-mock-backend:latest Built 
 Image apim-simulator:latest Built 
 Network apim-simulator_apim Creating 
 Network apim-simulator_apim Created 
 Container apim-simulator-hello-api-1 Creating 
 Container apim-simulator-mock-backend-1 Creating 
 Container apim-simulator-mock-backend-1 Created 
 Container apim-simulator-hello-api-1 Created 
 Container apim-simulator-apim-simulator-1 Creating 
 Container apim-simulator-apim-simulator-1 Created 
 Container apim-simulator-mock-backend-1 Starting 
 Container apim-simulator-hello-api-1 Starting 
 Container apim-simulator-mock-backend-1 Started 
 Container apim-simulator-hello-api-1 Started 
 Container apim-simulator-hello-api-1 Waiting 
 Container apim-simulator-hello-api-1 Healthy 
 Container apim-simulator-apim-simulator-1 Starting 
 Container apim-simulator-apim-simulator-1 Started 

## compose ps
NAME                              IMAGE                                COMMAND                  SERVICE          CREATED         STATUS                   PORTS
apim-simulator-apim-simulator-1   apim-simulator:latest                "sh -c '/app/.venv/b…"   apim-simulator   6 seconds ago   Up Less than a second    0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp
apim-simulator-hello-api-1        apim-simulator-hello-api:latest      "/app/.venv/bin/uvic…"   hello-api        6 seconds ago   Up 5 seconds (healthy)   8000/tcp
apim-simulator-mock-backend-1     apim-simulator-mock-backend:latest   "python server.py"       mock-backend     6 seconds ago   Up 5 seconds             8080/tcp

## startup
curl: (56) Recv failure: Connection reset by peer
```

Assessment:

- Strong match on the core APIM resource model. The runtime now exposes service, APIs, operations, products, subscriptions, backends, named values, version sets, policy fragments, users, and groups with stable service-scoped IDs.
- Strong match on beginner usability for local work. The compose flows are still simple, the gateway probes stay stable, and the management endpoints are easy to inspect.
- Good first match for teams migrating from AWS API Gateway. The new starter demonstrates stage-style paths, usage-plan-like product plus subscription behavior, and APIM-managed backend routing in a way that works live in Docker.
- Good match on Docker and platform-style runtime expectations. The code still honors `PORT`, `APIM_CONFIG_PATH`, `/apim/health`, `/apim/startup`, and tenant-key protected management routes.

Remaining gap versus the broader brief:
- The simulator is conceptually aligned to the Azure SDK surface, but it is not wire-compatible with ARM or the .NET management clients, which was intentionally deferred.
- The migration story is documented and demonstrated, but still manual rather than import-assisted.

```bash
set -euo pipefail
make down >/dev/null
printf 'Cleaned up compose stacks after review.\n'

```

```output
 Container apim-simulator-apim-simulator-1 Stopping 
 Container apim-simulator-apim-simulator-1 Stopped 
 Container apim-simulator-apim-simulator-1 Removing 
 Container apim-simulator-apim-simulator-1 Removed 
 Container apim-simulator-mock-backend-1 Stopping 
 Container apim-simulator-mock-backend-1 Stopped 
 Container apim-simulator-mock-backend-1 Removing 
 Container apim-simulator-mock-backend-1 Removed 
 Container apim-simulator-hello-api-1 Stopping 
 Container apim-simulator-hello-api-1 Stopped 
 Container apim-simulator-hello-api-1 Removing 
 Container apim-simulator-hello-api-1 Removed 
 Network apim-simulator_apim Removing 
 Network apim-simulator_apim Removed 
Cleaned up compose stacks after review.
```
