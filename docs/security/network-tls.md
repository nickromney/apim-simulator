# Local network, certificate and workload identity controls

The simulator implements the network and transport outcomes in Microsoft's
[security guide](https://learn.microsoft.com/en-us/azure/api-management/secure-api-management)
with socket-peer checks, a TLS terminator, an outbound firewall, a local CA and
signed workload identities. The executable lab is `examples/apim-security`.

## Network boundaries

`network_security.trusted_proxy_cidrs` grants forwarding authority to actual
socket peers. Uvicorn proxy-header rewriting is disabled so a supplied header
cannot change the peer used for this decision. Untrusted forwarding and client
certificate headers are removed. A trusted proxy's X-Forwarded-For chain is
walked from right to left, stopping at the first untrusted hop. The shipped
Nginx edge replaces incoming forwarding values and clears certificate headers;
the security lab forwards a certificate obtained from its own TLS handshake.
Use the edge's address as the trusted proxy, rather than the whole application
network.

When `service.public_network_access_enabled` is false or
`service.virtual_network_type` is `Internal`, only actual peers in
`network_security.private_peer_cidrs` can reach the gateway, including its
management endpoints. An empty peer list denies access. Rejected requests enter
the security audit sink. Health probes also need an approved local peer.

`network_security.allowed_backend_hosts` and `allowed_backend_cidrs` apply to
backend forwarding, policy HTTP callouts and resolvers, OIDC discovery, and
local vault requests. `null` leaves a restriction unconfigured; `[]` denies all
outgoing destinations. CIDR checks resolve the name and pin an approved address
for the connection while retaining the original Host header and TLS SNI. This
prevents a second DNS lookup from escaping the allowed network. Redirects are
checked independently. Governance in deny mode additionally requires HTTPS and
certificate chain/name verification for the final selected backend.

Root Compose host ports default to `127.0.0.1`; `APIM_BIND_ADDRESS` is an explicit
operator override. The TLS overlay serves redirects on its HTTP ports and
proxies only through TLS 1.2 or newer. Each selected API version independently
checks its allowed protocols and online status.

## Real certificate validation

`client_certificate` accepts a certificate from a trusted TLS terminator or an
ASGI TLS extension. Claims are derived from the certificate bytes. `ca_file`
provides trusted roots; `crl_file` provides signed revocation lists. Chain,
validity, and configured revocation checks fail closed. Copying a public
certificate into headers sent directly to the gateway does not establish
client identity.

The [validate-client-certificate policy](https://learn.microsoft.com/en-us/azure/api-management/validate-client-certificate-policy)
checks the documented validity, trust, revocation, and identity rules. Claims
on one identity are combined with AND; multiple identities use OR. Its default
revocation check requires a current signed local CRL for the chain's issuers.
This is the explicit local replacement for online revocation infrastructure.
The policy's ignore-error setting permits subsequent policies to execute.

Backend `ca_file`, `crl_file`, `client_certificate_file`, and
`client_certificate_key_file` configure actual TLS transports. Gateway
`certificates` entries select a stored certificate/key pair by policy ID or
thumbprint, using either certificate_file/key_file or key_vault_secret_id and
an optional key_vault_identity_client_id. Vault entries fetch a bounded base64
PKCS#12 bundle through signed identity, the outbound firewall and configured
TLS. A certificate and matching private key are required; password_env can
provide the bundle password without placing it in the tenant document. Temporary
PEM files are mode0600 and deleted immediately after SSLContext loading. Pool
keys include the vault bundle digest, so rotation changes the transport without
persisting fetched private-key material. Obsolete pools retire after active
response streams close; the reusable pool cache is limited to 128 entries.
Callout certificate selections remain scoped to the callout and cannot select a
credential for the main backend. Authentication-certificate inline PKCS#12 bodies are not provided by
this file-based certificate store. Missing credentials fail closed. Certificate
file or vault bundle changes create a fresh HTTP connection pool, so a reused connection cannot
keep presenting the previous credential after rotation.

## Signed local identity and vault

`workload_identity` defaults to `mode: signed`. It issues RS256 tokens using
`private_key_file` (or `APIM_WORKLOAD_IDENTITY_PRIVATE_KEY_FILE`), with issuer,
audience, identity, issued-at, not-before and expiry claims. `audience_grants`
explicitly grants each identity its permitted resources. The TLS lab backend
checks the signature and every claim, plus its accepted identity list. These
are local issuer credentials; Microsoft Entra credentials are not needed for
the lab's authentication outcome.

The local vault uses the same signed identity when
`APIM_LOCAL_VAULT_RESOURCE` is configured. Its TLS settings are
`APIM_LOCAL_VAULT_CA_FILE`, `APIM_LOCAL_VAULT_CRL_FILE`,
`APIM_LOCAL_VAULT_CLIENT_CERT_FILE`, and `APIM_LOCAL_VAULT_CLIENT_KEY_FILE`.
Key Vault named-value identity_client_id selects the local identity. OIDC's
optional `ca_file` supports a local issuer CA without disabling verification.
Vault values are snapshotted per gateway request, and subsequent requests read
rotated values without a gateway restart.

Header and opaque-token teaching adapters require explicit settings:
`network_security.allow_simulated_forwarded_headers`,
`client_certificate.allow_simulated_headers`, `workload_identity.mode: demo`,
backend `allow_simulated_certificate`, or gateway
`allow_simulated_certificate_authentication`. They default to disabled.

## Evidence

`tests/test_network_certificate_security.py` verifies forged-header rejection,
private socket peers, version protocols and offline versions, certificate
identity rules, signature/issuer/audience/expiry checks, HTTP callout and JWKS
firewalls, DNS pinning with actual TLS hostname verification, real handshakes
rejecting missing/untrusted/expired/revoked client certificates and TLS 1.1,
real backend mTLS, certificate rotation, and signed vault reads and rotation.
The lab's verifier tests published localhost ports and both local gateway
regions. Generated lab private keys are mode0600 and its CA is never installed
in the host trust store.
