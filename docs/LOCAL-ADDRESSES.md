# Local addresses and sslip.io

The default browser workflow uses three URLs:

- `http://localhost:3007` is the operator console, a static React app served
  from the `ui` nginx container on host loopback port 3007.
- `http://localhost:8000` is the gateway base URL and management API, served
  from the APIM simulator container on host loopback port 8000.
- `http://localhost:8000/apim/portal` is the consumer developer portal, served
  by the gateway itself as a page and API routes.

The root Compose files bind published ports to `127.0.0.1` by default. The
operator console makes browser requests to the gateway URL entered in its
connection field. Its **Load Local Demo** preset uses `http://localhost:8000`.
Those requests cross browser origins because port 3007 and port 8000 are
different origins, so the simulator's management CORS allowlist includes the
operator console origin. The consumer portal uses relative, same-origin routes
under `/apim/portal`, so it does not need a second portal hostname.

`localhost` is relative to the process making the request. In the user's
browser, it means the user's machine. Inside a container, `localhost` means
that same container. A sibling service on the Compose network should use the
service name, such as `http://apim-simulator:8000`, instead. An
`sslip.io` name containing `127.0.0.1` also resolves to loopback on the
machine performing DNS resolution; it does not create a route into a Docker
container or change which host port Docker publishes.

## Existing sslip.io use

The repository already uses these sslip.io names for workflows that benefit
from named hosts:

- `edge.apim.127.0.0.1.sslip.io` fronts the edge proxy on HTTP port 8088 with
  `make up-edge`, or TLS port 9443 with `make up-tls`.
  `apim.127.0.0.1.sslip.io` and names under
  `*.apim.127.0.0.1.sslip.io` are also configured for the edge virtual host.
- `lgtm.apim.127.0.0.1.sslip.io` fronts the Grafana UI through the local TLS
  proxy on port 8443.

The edge and Grafana proxies terminate TLS. `make up-edge` and `make up-tls`
run `ensure-certs`, which generates a local certificate with `mkcert`; the
certificate includes the default edge names and wildcard. The OTEL stacks use
that same certificate for Grafana; `make up-otel`, `make up-hello-otel` and
`make up-todo-otel` also run `ensure-certs` before starting their TLS proxy.
`make prereqs`
checks that `mkcert` and its local CA are installed, but does not generate the
certificate. This is local development trust, not a publicly trusted
certificate. These TLS routes are separate from the default operator console,
direct gateway, and consumer portal URLs, which use plain HTTP on ports 3007
and 8000.

The Make variables for edge hostnames feed the certificate generator and
smoke scripts, while the mounted Nginx configs still contain the default
hostnames and certificate path. Changing an edge hostname variable alone
does not reconfigure the proxy; the Nginx config and certificate mount must
also match.

sslip.io is a public DNS service that returns the IP address embedded in a
hostname, for example `edge.apim.127.0.0.1.sslip.io` to `127.0.0.1`. That
lookup depends on reaching a DNS resolver that can answer for sslip.io. It can
fail when offline, when a VPN or corporate resolver blocks or rewrites the
domain, or when local DNS settings do not reach the service. A local hosts
file entry or a local DNS zone can remove that DNS dependency, but then the
mapping must be maintained on each machine. See the
[sslip.io documentation](https://sslip.io/) for its hostname format and
current service details.

## Should the console and gateway switch to named addresses?

There is no clear default-workflow gain in changing these three URLs. The
console and gateway already use distinct ports, and the consumer portal shares
the gateway origin. A hostname alone does not add TLS; the existing TLS setup
is attached to the edge and Grafana proxy listeners, not to ports 3007 or
8000. Using names such as `console.apim.127.0.0.1.sslip.io` and
`api.apim.127.0.0.1.sslip.io` would also create new browser origins. CORS
matches the full scheme, hostname, and port, so the operator console origin
would need to be allowed explicitly, and the console's saved gateway URL
would need to use the intended gateway name. The `OPERATOR_CONSOLE_URL`,
`APIM_BASE_URL`, and `APIM_ALLOWED_ORIGIN_OPERATOR_CONSOLE` Make variables
can configure those values for stack commands; the UI's built-in demo
connection remains `http://localhost:8000` until changed in the UI.

Named loopback hosts are useful when a workflow specifically needs host-based
routing, distinct cookie or origin behavior, OIDC callback URLs, or local TLS
testing. For this repository those cases are already demonstrated by the
edge/TLS and Grafana routes. Keep localhost as the default for the operator
console, direct gateway, and consumer portal; use the existing named routes
when their host and TLS behavior is the subject of the exercise.

The sibling platform repository uses this pattern where several named sites
share a local TLS reverse proxy, and where its browser workflow tool needs a
stable HTTPS/HTTP2 origin. That setup gains virtual-host routing and origin
behavior from the names. Here, the console and gateway already have separate
host ports, while the consumer portal shares the gateway origin, so copying
that default would add DNS and CORS configuration without simplifying the
current route layout.

Treat a loopback sslip.io name as a browser-accessible alias to a local
service. It does not make that service private from pages a user may visit:
keep published services bound to loopback, preserve exact CORS origins, and
avoid trusting arbitrary `Host` or forwarded-host values at a proxy. CORS
controls whether browser JavaScript can read responses; it is not a network
access control. See OWASP's guidance on
[verifying web request origins](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html#using-standard-headers-to-verify-origin)
and [DNS rebinding risks in URL validation](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html#domain-name).
