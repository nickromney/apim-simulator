# Todo App Example

This example is the smallest full-stack app in the repository that proves the
browser-facing APIM path:

`Browser -> Astro frontend -> apim-simulator -> FastAPI todo API`

It is intentionally container-first and environment-configured so it can be
lifted into `platform/apps/todo` later without changing the application code.

## Local stack

```bash
make up-todo
make smoke-todo
make test-todo-e2e
make test-todo-bruno
make export-todo-har
make down
```

The browser entrypoint is `http://localhost:3000`. The APIM gateway is
`http://localhost:8000`.

## External client artifacts

- Bruno collection: `examples/todo-app/api-clients/bruno/`
- Proxyman HAR capture: `examples/todo-app/api-clients/proxyman/todo-through-apim.har`

The Bruno environment file defaults to localhost, but the base URL and
subscription key are just variables, so the same collection can be pointed at a
Kubernetes ingress later without changing the request files.

`make export-todo-har` regenerates the HAR from the currently running stack so
the Proxyman import reflects real requests and responses, including APIM proof
headers and the 401 auth cases.
