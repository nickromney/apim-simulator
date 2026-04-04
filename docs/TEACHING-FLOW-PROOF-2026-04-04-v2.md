# Teaching Flow Proof

*2026-04-04T14:23:44Z*

This document proves a concrete teaching flow from the repo works end to end.

Chosen flow:
- the hello starter from the training guide and starter recipe
- first in anonymous mode
- then in subscription-protected mode

Important note:
- the hello starter configs do not enable tenant management
- so `/apim/management/*` is expected to return `404 Not found` in this teaching flow
- that is a property of the starter config, not a runtime failure

```bash
sed -n '500,535p' docs/APIM-TRAINING-GUIDE.md
```

````output
- `examples/hello-api/apim.anonymous.json`
- `compose.hello.yml`

For the richer browser-backed example, look at:

- `examples/todo-app/api-fastapi-container-app/main.py`

Both are normal FastAPI apps. The APIM-specific work happens in front of them,
not instead of them.

If you want the most directive path, do this:

```bash
make up-hello
make smoke-hello
```

Then inspect:

- `examples/hello-api/main.py`
- `examples/hello-api/apim.anonymous.json`
- `compose.hello.yml`

That keeps the gateway as the public entrypoint and the backend as an internal
service, while giving you a concrete example that already runs.

### Add Observability At The Same Time

For Python backends in this repo, prefer the shared helper in `app/telemetry.py`
so logs, traces, and metrics follow the same contract as the gateway.

The todo backend is the reference implementation for that:

- `configure_observability(...)`
- `instrument_fastapi_app(...)`

````

```bash
sed -n '40,58p' docs/APIM-STARTER-RECIPE.md
```

````output
## Fastest Path

Run the anonymous version first:

```bash
make up-hello
make smoke-hello
```

That proves the basic route works before you add auth.

Switch to subscription-only:

```bash
make up-hello-subscription
SMOKE_HELLO_MODE=subscription make smoke-hello
```

Switch to JWT-only:
````

Step 1 runs the anonymous hello starter exactly as documented.

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
make up-hello >/dev/null
wait_for_startup
printf '\n## smoke script\n'
make smoke-hello
printf '\n## raw startup\n'
curl -i -sS http://localhost:8000/apim/startup
printf '\n## raw health\n'
curl -i -sS http://localhost:8000/api/health
printf '\n## raw hello\n'
curl -i -sS 'http://localhost:8000/api/hello?name=team'
printf '\n## management path in starter\n'
curl -i -sS -H 'X-Apim-Tenant-Key: local-dev-tenant-key' http://localhost:8000/apim/management/apis

```

```output
 Image apim-simulator:latest Building 
 Image apim-simulator-mock-backend:latest Building 
 Image apim-simulator-hello-api:latest Building 
 Image apim-simulator:latest Built 
 Image apim-simulator-mock-backend:latest Built 
 Image apim-simulator-hello-api:latest Built 
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
 Container apim-simulator-hello-api-1 Started 
 Container apim-simulator-mock-backend-1 Started 
 Container apim-simulator-hello-api-1 Waiting 
 Container apim-simulator-hello-api-1 Healthy 
 Container apim-simulator-apim-simulator-1 Starting 
 Container apim-simulator-apim-simulator-1 Started 

## smoke script
uv run python scripts/smoke_hello.py
hello smoke passed
- mode: anonymous
- /api/health: 200
- /api/hello: 200

## raw startup
HTTP/1.1 200 OK
date: Sat, 04 Apr 2026 14:23:55 GMT
server: uvicorn
content-length: 20
content-type: application/json
x-correlation-id: corr-c5ba0bee-152c-4d2c-b61f-4aa219aca1b1

{"status":"started"}
## raw health
HTTP/1.1 200 OK
date: Sat, 04 Apr 2026 14:23:55 GMT
server: uvicorn
date: Sat, 04 Apr 2026 14:23:55 GMT
server: uvicorn
content-length: 37
content-type: application/json
x-apim-simulator: apim-simulator
x-correlation-id: corr-b463de8e-6867-4299-965d-b7d81acf8ce8

{"status":"ok","service":"hello-api"}
## raw hello
HTTP/1.1 200 OK
date: Sat, 04 Apr 2026 14:23:55 GMT
server: uvicorn
date: Sat, 04 Apr 2026 14:23:55 GMT
server: uvicorn
content-length: 25
content-type: application/json
x-apim-simulator: apim-simulator
x-correlation-id: corr-4fb58b9c-1f11-4258-a11b-9f5e61705180

{"message":"hello, team"}
## management path in starter
HTTP/1.1 404 Not Found
date: Sat, 04 Apr 2026 14:23:55 GMT
server: uvicorn
content-length: 22
content-type: application/json
x-correlation-id: corr-be2287f0-9be6-4fba-b0b5-2d4355cf002c

{"detail":"Not found"}```
```

Step 2 switches to the subscription-protected hello starter and proves the three beginner-visible outcomes: missing key rejected, invalid key rejected, valid key accepted.

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
make up-hello-subscription >/dev/null
wait_for_startup
printf '\n## smoke script\n'
SMOKE_HELLO_MODE=subscription make smoke-hello
printf '\n## missing key\n'
curl -i -sS http://localhost:8000/api/health
printf '\n## invalid key\n'
curl -i -sS -H 'Ocp-Apim-Subscription-Key: hello-demo-key-invalid' http://localhost:8000/api/health
printf '\n## valid key\n'
curl -i -sS -H 'Ocp-Apim-Subscription-Key: hello-demo-key' 'http://localhost:8000/api/hello?name=subscription'
printf '\n## management path in starter\n'
curl -i -sS -H 'X-Apim-Tenant-Key: local-dev-tenant-key' http://localhost:8000/apim/management/apis

```

```output
 Image apim-simulator:latest Building 
 Image apim-simulator-hello-api:latest Building 
 Image apim-simulator-mock-backend:latest Building 
 Image apim-simulator-mock-backend:latest Built 
 Image apim-simulator:latest Built 
 Image apim-simulator-hello-api:latest Built 
 Network apim-simulator_apim Creating 
 Network apim-simulator_apim Created 
 Container apim-simulator-mock-backend-1 Creating 
 Container apim-simulator-hello-api-1 Creating 
 Container apim-simulator-hello-api-1 Created 
 Container apim-simulator-mock-backend-1 Created 
 Container apim-simulator-apim-simulator-1 Creating 
 Container apim-simulator-apim-simulator-1 Created 
 Container apim-simulator-hello-api-1 Starting 
 Container apim-simulator-mock-backend-1 Starting 
 Container apim-simulator-mock-backend-1 Started 
 Container apim-simulator-hello-api-1 Started 
 Container apim-simulator-hello-api-1 Waiting 
 Container apim-simulator-hello-api-1 Healthy 
 Container apim-simulator-apim-simulator-1 Starting 
 Container apim-simulator-apim-simulator-1 Started 

## smoke script
uv run python scripts/smoke_hello.py
hello smoke passed
- mode: subscription
- missing subscription: 401
- invalid subscription: 401
- valid subscription: 200

## missing key
HTTP/1.1 401 Unauthorized
date: Sat, 04 Apr 2026 14:24:08 GMT
server: uvicorn
content-length: 37
content-type: application/json
x-correlation-id: corr-fc97c8e6-7d5c-472d-9476-045464088aef

{"detail":"Missing subscription key"}
## invalid key
HTTP/1.1 401 Unauthorized
date: Sat, 04 Apr 2026 14:24:08 GMT
server: uvicorn
content-length: 37
content-type: application/json
x-correlation-id: corr-4c76c9b3-f84a-42ba-a7ee-3168288834b9

{"detail":"Invalid subscription key"}
## valid key
HTTP/1.1 200 OK
date: Sat, 04 Apr 2026 14:24:08 GMT
server: uvicorn
date: Sat, 04 Apr 2026 14:24:09 GMT
server: uvicorn
content-length: 33
content-type: application/json
x-apim-simulator: apim-simulator
x-correlation-id: corr-de24e23b-3a5b-4fcc-a2fb-1b089c61a2b2
x-hello-policy: applied

{"message":"hello, subscription"}
## management path in starter
HTTP/1.1 404 Not Found
date: Sat, 04 Apr 2026 14:24:08 GMT
server: uvicorn
content-length: 22
content-type: application/json
x-correlation-id: corr-3b547e0b-5f43-4f7b-9fce-8bc4225aec92

{"detail":"Not found"}```
```

Result:

- The documented beginner flow works live in Docker.
- Anonymous mode proves a new API can be stood up behind APIM quickly.
- Subscription mode proves the same starter can immediately teach products, keys, and policy effects.
- The starter keeps the teaching surface focused: gateway path, auth behavior, and policy effect first; management-plane teaching can be layered on separately with configs that enable tenant access.

```bash
set -euo pipefail
make down >/dev/null 2>&1 || true
printf 'Cleaned up compose stacks after teaching-flow proof.\n'

```

```output
Cleaned up compose stacks after teaching-flow proof.
```
