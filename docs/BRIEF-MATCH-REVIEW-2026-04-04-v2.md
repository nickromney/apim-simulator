# APIM Brief Match Review

*2026-04-04T14:19:27Z*

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

======================== 97 passed, 1 skipped in 3.81s =========================
```

Next, prove the default direct-public Docker Compose stack boots and exposes both gateway traffic and the new APIM-style management resource surface.

```bash
set -euo pipefail
wait_for_startup() {
  for _ in $(seq 1 30); do
    if curl -fsS http://localhost:8000/apim/startup >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}
make down >/dev/null 2>&1 || true
make up >/dev/null
wait_for_startup
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
 Image apim-simulator-mock-backend:latest Building 
 Image apim-simulator:latest Building 
 Image apim-simulator-mock-backend:latest Built 
 Image apim-simulator:latest Built 
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
NAME                              IMAGE                                COMMAND                  SERVICE          CREATED         STATUS         PORTS
apim-simulator-apim-simulator-1   apim-simulator:latest                "sh -c '/app/.venv/b…"   apim-simulator   4 seconds ago   Up 3 seconds   0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp
apim-simulator-mock-backend-1     apim-simulator-mock-backend:latest   "python server.py"       mock-backend     4 seconds ago   Up 3 seconds   8080/tcp

## startup
HTTP/1.1 200 OK
date: Sat, 04 Apr 2026 14:19:37 GMT
server: uvicorn
content-length: 20
content-type: application/json
x-correlation-id: corr-3bf29e75-2853-4e4d-86a3-53b760cd6f50

{"status":"started"}
## gateway echo
HTTP/1.1 200 OK
date: Sat, 04 Apr 2026 14:19:37 GMT
server: uvicorn
server: apim-simulator-mock-backend/0.1 Python/3.13.12
date: Sat, 04 Apr 2026 14:19:37 GMT
content-type: application/json
content-length: 1218
x-apim-simulator: apim-simulator
x-correlation-id: corr-10bd0887-95b3-49fa-974e-84d29eb674dc

{"ok": true, "method": "GET", "path": "/api/echo", "headers": {"host": "localhost:8000", "Accept-Encoding": "gzip, deflate", "Connection": "keep-alive", "user-agent": "curl/8.7.1", "accept": "*/*", "x-apim-user-object-id": "anon-demo", "x-apim-user-email": "demo@dev.test", "x-apim-user-name": "Demo User", "x-apim-auth-method": "oidc", "x-ms-client-principal": "eyJhdXRoX3R5cCI6ICJvYXV0aDIiLCAibmFtZV90eXAiOiAiaHR0cDovL3NjaGVtYXMueG1sc29hcC5vcmcvd3MvMjAwNS8wNS9pZGVudGl0eS9jbGFpbXMvbmFtZWlkZW50aWZpZXIiLCAicm9sZV90eXAiOiAiaHR0cDovL3NjaGVtYXMubWljcm9zb2Z0LmNvbS93cy8yMDA4LzA2L2lkZW50aXR5L2NsYWltcy9yb2xlIiwgImNsYWltcyI6IFt7InR5cCI6ICJodHRwOi8vc2NoZW1hcy54bWxzb2FwLm9yZy93cy8yMDA1LzA1L2lkZW50aXR5L2NsYWltcy9uYW1laWRlbnRpZmllciIsICJ2YWwiOiAiYW5vbi1kZW1vIn0sIHsidHlwIjogImh0dHA6Ly9zY2hlbWFzLnhtbHNvYXAub3JnL3dzLzIwMDUvMDUvaWRlbnRpdHkvY2xhaW1zL25hbWUiLCAidmFsIjogIkRlbW8gVXNlciJ9LCB7InR5cCI6ICJodHRwOi8vc2NoZW1hcy54bWxzb2FwLm9yZy93cy8yMDA1LzA1L2lkZW50aXR5L2NsYWltcy9lbWFpbGFkZHJlc3MiLCAidmFsIjogImRlbW9AZGV2LnRlc3QifSwgeyJ0eXAiOiAicHJlZmVycmVkX3VzZXJuYW1lIiwgInZhbCI6ICJkZW1vQGRldi50ZXN0In1dfQ==", "x-ms-client-principal-name": "demo@dev.test", "x-correlation-id": "corr-10bd0887-95b3-49fa-974e-84d29eb674dc"}, "body": ""}
## management apis
HTTP/1.1 200 OK
date: Sat, 04 Apr 2026 14:19:37 GMT
server: uvicorn
content-length: 1278
content-type: application/json
x-correlation-id: corr-64d5d039-5237-405e-8933-c78e3d672c02

[{"id":"default","resource_id":"service/apim-simulator/apis/default","name":"Default","path":"api","upstream_base_url":"http://mock-backend:8080","upstream_path_prefix":"/api","backend":null,"products":["default"],"api_version_set":null,"api_version":null,"subscription_header_names":null,"subscription_query_param_names":null,"policy_scope":{"scope_type":"api","scope_name":"default"},"operations":[{"id":"health","resource_id":"service/apim-simulator/apis/default/operations/health","api_id":"default","name":"Health","method":"GET","url_template":"/health","upstream_base_url":null,"upstream_path_prefix":null,"backend":null,"products":null,"api_version_set":null,"api_version":null,"subscription_header_names":null,"subscription_query_param_names":null,"authz":null,"policy_scope":{"scope_type":"operation","scope_name":"default:health"}},{"id":"echo","resource_id":"service/apim-simulator/apis/default/operations/echo","api_id":"default","name":"Echo","method":"GET","url_template":"/echo","upstream_base_url":null,"upstream_path_prefix":null,"backend":null,"products":null,"api_version_set":null,"api_version":null,"subscription_header_names":null,"subscription_query_param_names":null,"authz":null,"policy_scope":{"scope_type":"operation","scope_name":"default:echo"}}]}]```
```

Then switch the running hello stack to the new "Migrating From AWS API Gateway" starter config and validate the expected auth behavior and the stage-like `/prod/...` API path.

```bash
set -euo pipefail
wait_for_startup() {
  for _ in $(seq 1 30); do
    if curl -fsS http://localhost:8000/apim/startup >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}
make down >/dev/null 2>&1 || true
HELLO_APIM_CONFIG_PATH=/app/examples/migrating-from-aws-api-gateway/apim.http-api.json make up-hello >/dev/null
wait_for_startup
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
apim-simulator-apim-simulator-1   apim-simulator:latest                "sh -c '/app/.venv/b…"   apim-simulator   8 seconds ago   Up 2 seconds             0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp
apim-simulator-hello-api-1        apim-simulator-hello-api:latest      "/app/.venv/bin/uvic…"   hello-api        8 seconds ago   Up 8 seconds (healthy)   8000/tcp
apim-simulator-mock-backend-1     apim-simulator-mock-backend:latest   "python server.py"       mock-backend     8 seconds ago   Up 8 seconds             8080/tcp

## startup
HTTP/1.1 200 OK
date: Sat, 04 Apr 2026 14:19:49 GMT
server: uvicorn
content-length: 20
content-type: application/json
x-correlation-id: corr-33049599-cd41-451a-bbdc-cf363393a53b

{"status":"started"}
## no subscription key
HTTP/1.1 401 Unauthorized
date: Sat, 04 Apr 2026 14:19:49 GMT
server: uvicorn
content-length: 37
content-type: application/json
x-correlation-id: corr-8acd47cc-0da9-470b-8836-0c5516c8e69a

{"detail":"Missing subscription key"}
## health with subscription key
HTTP/1.1 200 OK
date: Sat, 04 Apr 2026 14:19:49 GMT
server: uvicorn
date: Sat, 04 Apr 2026 14:19:50 GMT
server: uvicorn
content-length: 37
content-type: application/json
x-apim-simulator: apim-simulator
x-correlation-id: corr-4d3874b9-322f-4992-8597-c3abae451624

{"status":"ok","service":"hello-api"}
## hello with subscription key
HTTP/1.1 200 OK
date: Sat, 04 Apr 2026 14:19:49 GMT
server: uvicorn
date: Sat, 04 Apr 2026 14:19:50 GMT
server: uvicorn
content-length: 24
content-type: application/json
x-apim-simulator: apim-simulator
x-correlation-id: corr-bee90ab4-ae97-492a-8819-e858c533d142

{"message":"hello, aws"}
## management service
HTTP/1.1 200 OK
date: Sat, 04 Apr 2026 14:19:49 GMT
server: uvicorn
content-length: 1210
content-type: application/json
x-correlation-id: corr-b6cff0da-200c-47be-8620-aa98a804ff36

{"id":"service/aws-api-gateway-migration","name":"aws-api-gateway-migration","display_name":"Migrating From AWS API Gateway","gateway_policy_scope":{"scope_type":"gateway","scope_name":"gateway"},"counts":{"routes":2,"apis":1,"operations":2,"products":1,"subscriptions":1,"backends":1,"named_values":1,"api_version_sets":0,"policy_fragments":1,"users":0,"groups":0,"recent_traces":0},"management":{"tenant_access_enabled":true,"status_path":"/apim/management/status","summary_path":"/apim/management/summary"},"tracing":{"enabled":false,"lookup_path_template":"/apim/trace/{trace_id}","recent_path":"/apim/management/traces"},"routes":[{"name":"http-api:health","path_prefix":"/prod/health","host_match":[],"methods":["GET"],"upstream_base_url":"http://hello-api:8000","upstream_path_prefix":"/api/health","backend":"hello-backend","product":null,"products":["starter-usage-plan"],"api_version_set":null,"api_version":null},{"name":"http-api:hello","path_prefix":"/prod/hello","host_match":[],"methods":["GET"],"upstream_base_url":"http://hello-api:8000","upstream_path_prefix":"/api/hello","backend":"hello-backend","product":null,"products":["starter-usage-plan"],"api_version_set":null,"api_version":null}]}```
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
make down >/dev/null 2>&1 || true
printf 'Cleaned up compose stacks after review.\n'

```

```output
Cleaned up compose stacks after review.
```
