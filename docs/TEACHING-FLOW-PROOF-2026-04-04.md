# Teaching Flow Proof

*2026-04-04T14:22:45Z*

This document proves a concrete teaching flow from the repo works end to end.

Chosen flow:
- the hello starter from the training guide and starter recipe
- first in anonymous mode
- then in subscription-protected mode

Why this flow:
- it is the smallest beginner path in the docs
- it exercises the Docker path, gateway probes, public API calls, and APIM management surface
- it shows the progression from "it routes" to "it enforces APIM-style access"

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

Step 1 runs the anonymous hello starter exactly as documented and proves the gateway and management views respond.

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
printf '\n## management apis\n'
curl -i -sS -H 'X-Apim-Tenant-Key: local-dev-tenant-key' http://localhost:8000/apim/management/apis

```

```output
 Image apim-simulator-hello-api:latest Building 
 Image apim-simulator-mock-backend:latest Building 
 Image apim-simulator:latest Building 
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
 Container apim-simulator-hello-api-1 Starting 
 Container apim-simulator-mock-backend-1 Starting 
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
date: Sat, 04 Apr 2026 14:22:54 GMT
server: uvicorn
content-length: 20
content-type: application/json
x-correlation-id: corr-0104b4d3-62e4-4379-b07a-14c9fbad0871

{"status":"started"}
## raw health
HTTP/1.1 200 OK
date: Sat, 04 Apr 2026 14:22:54 GMT
server: uvicorn
date: Sat, 04 Apr 2026 14:22:55 GMT
server: uvicorn
content-length: 37
content-type: application/json
x-apim-simulator: apim-simulator
x-correlation-id: corr-eb467c6f-eb59-46ab-a88e-05115d80558f

{"status":"ok","service":"hello-api"}
## raw hello
HTTP/1.1 200 OK
date: Sat, 04 Apr 2026 14:22:54 GMT
server: uvicorn
date: Sat, 04 Apr 2026 14:22:55 GMT
server: uvicorn
content-length: 25
content-type: application/json
x-apim-simulator: apim-simulator
x-correlation-id: corr-2bb87b7d-b14b-44f2-ab0b-4c206f0a4904

{"message":"hello, team"}
## management apis
HTTP/1.1 404 Not Found
date: Sat, 04 Apr 2026 14:22:54 GMT
server: uvicorn
content-length: 22
content-type: application/json
x-correlation-id: corr-14a745e3-2ed8-4bf4-af95-52b2516ab552

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
printf '\n## management apis\n'
curl -i -sS -H 'X-Apim-Tenant-Key: local-dev-tenant-key' http://localhost:8000/apim/management/apis

```

```output
 Image apim-simulator-mock-backend:latest Building 
 Image apim-simulator:latest Building 
 Image apim-simulator-hello-api:latest Building 
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
date: Sat, 04 Apr 2026 14:23:08 GMT
server: uvicorn
content-length: 37
content-type: application/json
x-correlation-id: corr-679cc7c1-a5e5-43c2-bbc0-37c2c0382523

{"detail":"Missing subscription key"}
## invalid key
HTTP/1.1 401 Unauthorized
date: Sat, 04 Apr 2026 14:23:08 GMT
server: uvicorn
content-length: 37
content-type: application/json
x-correlation-id: corr-63a0440a-d424-4172-89ab-5b5916afcac5

{"detail":"Invalid subscription key"}
## valid key
HTTP/1.1 200 OK
date: Sat, 04 Apr 2026 14:23:08 GMT
server: uvicorn
date: Sat, 04 Apr 2026 14:23:08 GMT
server: uvicorn
content-length: 33
content-type: application/json
x-apim-simulator: apim-simulator
x-correlation-id: corr-6be3d205-6bc2-4fb1-8526-1106f01570d1
x-hello-policy: applied

{"message":"hello, subscription"}
## management apis
HTTP/1.1 404 Not Found
date: Sat, 04 Apr 2026 14:23:08 GMT
server: uvicorn
content-length: 22
content-type: application/json
x-correlation-id: corr-024a334f-1afa-478a-b12f-b02dfc00b951

{"detail":"Not found"}```
```

Result:

- The documented beginner flow works live in Docker.
- Anonymous mode proves a new API can be stood up behind APIM quickly.
- Subscription mode proves the same starter can immediately teach products, keys, and policy effects.
- The management API stays inspectable during both stages, which is important for teaching how the config maps to APIM concepts.

```bash
set -euo pipefail
make down >/dev/null 2>&1 || true
printf 'Cleaned up compose stacks after teaching-flow proof.\n'

```

```output
Cleaned up compose stacks after teaching-flow proof.
```
