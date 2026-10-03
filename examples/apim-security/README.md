# Local APIM security lab

This lab exercises the security outcomes in Microsoft's
[secure APIM deployment guide](https://learn.microsoft.com/en-us/azure/api-management/secure-api-management)
with disposable local identities and certificates. It runs two gateways on a
private Docker network, a TLS ingress, and a separate backend/vault requiring
both a real client certificate and a signed workload token.

Run from the repository:

```sh
make -C examples/apim-security up
make -C examples/apim-security verify
make -C examples/apim-security down
```

`up` generates a local CA, client/server certificates, CRL, workload signing
keys and encrypted tenant configuration under ignored `.runtime/`. Encryption
and identity signing keys stay stable across launches. The lab maps containers
to the current user's UID/GID to read private key files without making them
world readable. `prepare` regenerates certificates; restart the lab after it.

The published gateway is `https://localhost:<SECURITY_GATEWAY_PORT>` (default
8943); its CA is `.runtime/certs/ca.pem`. The backend's test port defaults to
8944 on loopback and rejects callers without a trusted client certificate and
signed token. Gateway containers have no published port. Override
`SECURITY_GATEWAY_PORT` and `SECURITY_BACKEND_PORT` for collisions. The
`SECURITY_EDGE_PRIVATE_IP` input must match the private network subnet when
changed; only this peer is trusted to attest forwarded client certificates.

The verification runner checks authorized requests, public trace/reload
rejection, scoped roles, MFA/device conditions, WAF/body limits, resource locks,
encrypted backup/restore, durable actor attribution, incoming and backend
certificates, and continued requests while one gateway is stopped. Unit tests
also cover certificate expiry/revocation/rotation, TLS versions, outbound
redirects/DNS restrictions, audit retention and concurrency bounds.

Use signed management tokens with the issuer `https://apim.local/management`,
audience `apim-management`, a subject, issued/expiry timestamps and an allowed
role. Administrative roles also require `amr: ["mfa"]` and
`device_compliant: true`. The disposable issuer signing key is in
`.runtime/lab.env`; the verification runner shows the complete token shape.
This models conditional access by validating trusted issuer claims; it does
not create a real MFA service or a device enrollment service.

Each gateway has its own encrypted configuration and audit database. The edge
provides health-based local region failover. This demonstrates independent
failure domains on one machine; replication of subsequent management edits is
performed with encrypted backup/restore. It does not claim geographic disaster
protection or cloud DDoS capacity.

Existing teaching examples explicitly enable simulated headers, unsigned demo
identities or unauthenticated traces when their workflows require them. Secure
configurations leave those compatibility settings disabled.
