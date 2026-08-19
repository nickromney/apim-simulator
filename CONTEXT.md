# Domain glossary

Terms used by the simulator and by architecture reviews.

| Term | Meaning |
| --- | --- |
| APIM service | The gateway runtime that receives HTTP and applies policy before the backend. |
| API | A published surface with a base path, authored in config. |
| Operation | A method and URL template under an API. |
| Product | A named access bundle. Policies merge global → product → API → operation. |
| Subscription | A client key pair granted to products. |
| Backend | The upstream the gateway calls. A pool selects members by weight, priority, and circuit breaker. |
| Effective policy | The stacked XML document that actually fires for a call. |
| Request pipeline | Product grant, policy stack, backend pick, cache, retry, and trace attach. |
| Management plane | Tenant mutations (persist, reload, CRUD). The HTTP router is an adapter over it. |
| Tenant document | `GatewayConfig` — the only field catalog. Import and projection are adapters. |
