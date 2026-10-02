# Local tutorial simulation research

Reviewed the eleven Microsoft Learn tutorials on 2026-10-02. The intended
outcome is to rehearse the workflows locally; Azure-hosted services describe
the behavior to simulate, rather than exclusions from the simulator.

The first live run found stale assumptions about HTTP OpenAPI servers,
rate-limit bodies, correlation response headers, and revision snapshots.
After correcting these, all eleven existing adapted scripts passed through
published localhost ports, including actual Prometheus, Loki and Tempo exports.
That established a baseline, not completion of the official workflows.

Additional observed gaps: both public Petstore imports fail, new product
defaults differ, product updates omit limits and legal terms, tracing lacks
API-scoped expiring credentials, the portal lacks draft/published customization,
and API inventory exports lack a continuously synchronized catalog.

Implementation follows existing FastAPI routers, Pydantic configuration,
management persistence, local traces and the consumer portal. Preserve policy
results and bodies under tracing. Keep state local and tenant-key-protect
administrative writes. Use the existing UI/API conventions and no new runtime
dependencies. Verify each workflow through regression tests and published ports.

Client compatibility is separate from the observable workflows. The default
target is local UI/API equivalents; unchanged Azure clients may need an
additional ARM compatibility surface if requested.

References are the eleven source links in the [tutorial index](README.md).
