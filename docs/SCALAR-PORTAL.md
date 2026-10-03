# Local Scalar portal

Scalar API Reference and its embedded request client run inside the native
developer portal at `/apim/portal`. Product visibility, identities, subscription
approval and key management remain simulator behaviour.

## Local workflow

The reproducible browser lab is `make -C examples/portal up`, followed by
`make -C examples/portal smoke` and `make -C examples/portal down`.

Run `make up` and open <http://localhost:8000/apim/portal>. Select an API,
version and subscription key, then use the **Explore an API** section
to inspect schemas, edit parameters and bodies, and send requests. **Reload API
reference** fetches the current contract after a management edit. The existing
quick operation tester is available in **Quick operation check** below it.

`make up` also starts **Manage APIs** on <http://localhost:3007>, where APIs
are created and configured. Both surfaces link to each other and offer
**Light**, **Dark**, and **System** appearance. Each origin saves only the
appearance preference; System follows operating-system changes. Demo access
is labelled separately from verified token access. **Sign out** removes the
portal token, visible private data, selected keys, and Scalar frame.

Definitions come from `GET /apim/portal/apis/{api_id}/openapi`, with the same
signed portal identity or explicitly enabled legacy user header as the product
catalogue. An API must belong to a product visible to that identity. Missing
and invisible APIs both return 404. Definitions exclude upstream targets and
policies, mask secret named values, and contain no subscription credentials.
Segment, header and query version selectors are exported into the contract.

The reference runs in a separate same-origin frame to isolate Scalar's styling.
The parent passes a contract and selected gateway key in memory after checking
visibility. Changing identity, API, version or key discards the frame. Portal
tokens are never supplied to Scalar. Browser storage of credentials is disabled.
The frame's CSP and request adapter restrict calls to the gateway origin;
external OAuth services require a separately configured client workflow.

## Offline assets and maintenance

`app/static/scalar/manifest.json` pins the official npm browser distribution,
its release timestamp, npm SHA-512 integrity and uncompressed bundle SHA-256.
`scalar.js.gz` is the deterministic gzip of that distribution's standalone
browser file. It is checked in so a clone, Python wheel, Docker image and
runtime archive can all serve the portal without fetching a CDN or installing
Node dependencies. The upstream MIT license accompanies it.

Reproduce the existing asset with:

```sh
uv run --extra dev python scripts/vendor_scalar.py
uv run --extra dev pytest tests/test_scalar_portal.py tests/test_catalog_metadata.py
```

To update, choose an upstream release at least seven days old, review its release
notes and browser dependencies, update the manifest from the official npm
metadata and extracted browser hash, then run the commands above. Review license
changes and repeat the browser smoke with external network access blocked.
Integrity verification occurs before extracting the one bundle member; no npm
install scripts run. The gzip asset is marked generated/binary in Git.

Cloud Agent, telemetry, hosted fonts and credential persistence are explicitly
disabled. No hosted proxy is configured. Offline here means internet-independent
local documentation and API calls; the gateway and required local backends must
still be running. Scalar's separate desktop client and framework Watch Mode are
upstream options, not services this project needs to host.

## Optional catalogue metadata

`catalog-info.yaml` keeps only component discovery metadata. API definitions
come from the running simulator, not copied YAML. Repository tests compare its
name with `pyproject.toml` and its links with registered application routes.
No Backstage app, Node backend, container or dependency graph is shipped.
