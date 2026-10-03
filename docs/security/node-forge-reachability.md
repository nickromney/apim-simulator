# node-forge advisory reachability, 2026-10-02

Historical evidence: the bundled Backstage app was removed in the Scalar portal migration.
Its dependencies and local patches are no longer shipped. The results below describe
the earlier Backstage build, not the current runtime.

The current Backstage lockfile resolves `node-forge` 1.4.0 through
`@backstage/backend-defaults` 0.18.0 and development dependencies.
[GHSA-86w9-cpqp-85rv](https://github.com/advisories/GHSA-86w9-cpqp-85rv)
affects RSA PKCS#1 v1.5 signature verification with malformed nested digest
algorithm elements; the advisory lists no patched release as of this review.
The base version remains flagged by version scanners. A local Yarn patch now
backports the nested element-count check from the open
[upstream PR1152](https://github.com/digitalbazaar/forge/pull/1152/files).
This is an unreleased backport, not a maintainer-published fixed version.
This review found no path from an
untrusted network certificate or token to the affected verifier in the configured
Backstage backend.

Evidence checked against installed dependency source and the committed app:

- `backstage/app/app-config.yaml` and `app-config.production.yaml` configure an
  HTTP backend and omit `backend.https`. `compose.backstage.yml` defaults
  `BACKSTAGE_BASE_URL` to `http://localhost:7007`, binding the host port to
  localhost by default.
- Backend defaults `dist/entrypoints/rootHttpRouter/http/config.cjs.js` enables
  certificate generation only when `backend.https: true` is configured.
  `createHttpServer.cjs.js:createServer` otherwise selects Node's HTTP server.
- `getGeneratedCertificate.cjs.js:getGeneratedCertificate` is the only functional
  `node-forge` import in backend defaults. It reads a local development PEM,
  parses it with `pki.certificateFromPem` to inspect expiry, or generates a local
  self-signed certificate through `selfsigned`.
- `selfsigned/index.js` signs the generated certificate with its generated
  private key and passes that same locally generated certificate into
  `pki.verifyCertificateChain`. It accepts no peer certificate or request body
  from this backend caller. A manually configured HTTPS certificate goes to
  Node's HTTPS server rather than a Forge peer verification path.
- Backstage default auth's user, plugin, and external JWKS token handlers import
  `jose` and call `jose.jwtVerify`, rather than Forge's RSA verifier. The backend
  entrypoint registers the app, auth, guest, catalog, and catalog-log plugins;
  it adds no custom Forge verification code.

The patch checks the nested AlgorithmIdentifier contains exactly its OID and
optional NULL after the existing ASN.1 validation. Four actual RSA verification
regressions retain valid SHA256 signatures with and without NULL parameters and
reject three forms of extra nested children. All three malformed cases failed
before the patch and pass after installation. The test uses a disposable
generated exponent-3 key; it tests malformed signatures rather than claiming to
reproduce a private-key-free forgery. The patch persists in the lockfile and is
included by the existing Docker build's `.yarn` copy.

Keep the advisory visible in dependency review. Reassess reachability before enabling generated development HTTPS,
adding certificate-verification plugins, changing the Backstage backend auth
implementation, or exposing additional development services. Replace or update
the dependency when upstream ships a supported fix.
