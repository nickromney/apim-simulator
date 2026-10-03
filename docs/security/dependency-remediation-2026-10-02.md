# Dependency remediation, 2026-10-02

The runtime Python scan and both npm application scans report zero known
advisories. Backstage's complete recursive scan retains **eight advisories:
one high, six moderate, one low, and zero critical** in the recorded scan. Each remaining advisory is
triaged below; none is silently suppressed. The mixed-slash React Router
navigation defect is mitigated by a local upstream backport; the version-only
scanner continues to report its advisory. The scan also emits 17 deprecated
package notices, which are counted separately. These are dated scanner results,
not a claim that every dependency is free of vulnerabilities.

Follow-up mitigation: the high-severity Forge defect now also has a local Yarn
backport from the open [upstream PR1152](https://github.com/digitalbazaar/forge/pull/1152/files).
The four RSA verification regressions pass; the three malformed nested structures
were accepted before the patch. Valid signatures still verify. This unreleased
backport closes the reported parser gap while the base version remains flagged.
The JSON scanner snapshots predate this follow-up patch and retain their original
lockfile hashes. Run `yarn test:security` to exercise both backports.

The original Backstage audit contained 276 scanner records, including 21
deprecation notices. Its 255 advisory records are compared to the eight remaining
advisories here; scanner records do not establish independently exploitable
defects. Final versions and dependency paths are
in [the sanitized Backstage evidence](backstage-advisories-2026-10-02.json);
[the final scanner summary](dependency-remediation-2026-10-02.json) includes Python,
UI and Astro results. The original snapshot remains in
[dependency-audit-2026-10-02.json](dependency-audit-2026-10-02.json).

## Changes and verification

Updated the direct Backstage packages and their compatible transitive ranges.
Retained deliberate resolutions for `got` 11.8.6, `@grpc/grpc-js` 1.14.5,
`adm-zip` 0.6.1, `basic-ftp` 6.2.1, and the affected exact `undici` 7.29.0
specifier to 7.30.0. The final review additionally applied these narrow overrides:

| Installed change | Purpose and primary advisory |
| --- | --- |
| `@tootallnate/once` 2.0.0 → 2.0.1 | Fix the abort-signal promise control-flow defect. [GHSA-vpq2-c234-7xj6](https://github.com/advisories/GHSA-vpq2-c234-7xj6) |
| Legacy `prismjs` 1.27.0 → 1.30.0 | Fix DOM clobbering in the syntax renderer used through `refractor` 3.6.0. [GHSA-x7hr-w5r2-h6wg](https://github.com/advisories/GHSA-x7hr-w5r2-h6wg) |
| `@ungap/structured-clone` 1.3.0 → 1.3.1 | Resolve the deprecated version's potential unsafe-deserialization notice. This was a package notice, not a separately numbered advisory. |

A Yarn patch backports the upstream MIT-licensed mixed-separator normalization
from [React Router PR15176](https://github.com/remix-run/react-router/pull/15176)
to `@remix-run/router` 1.23.4, including CommonJS, ES module, both UMD distributions,
and the shipped TypeScript source. Its `removeDoubleSlashes` now normalizes runs
of slashes/backslashes consistently before building internal navigation paths.
The five regressions in `backstage/app/scripts/security-router.test.cjs` check
all three mixed-separator attacks through actual `Link` rendering and
`BrowserRouter`/`useNavigate`, plus ordinary paths and deliberately explicit
external links. Four tests failed before the patch; all five pass afterward.
The Yarn patch is committed under `.yarn/patches` and applied by installation.

Yarn installation, the Backstage backend production build, and the Backstage app
production build all passed after these overrides. UI and Astro `npm audit --json`
were repeated after the dependency changes and both reported zero vulnerabilities.
The Backstage audit deliberately returns exit code 1 while these eight advisories
remain. Catalog, API documentation and authentication features remain installed.

## Remaining advisories and reachability

| Package/version; severity | Affected operation, local reachability and disposition |
| --- | --- |
| `node-forge` 1.4.0; **high** | The reported nested DigestAlgorithm parser gap is mitigated by a local unreleased upstream backport, verified with malformed and valid RSA signatures. No published patched release is listed. The configured backend runs HTTP; its Forge dependency is used for local development certificate generation/expiry checks, while JWT verification uses `jose`. No untrusted request-to-Forge-verifier path was found in this configuration. Keep the installed advisory visible and reassess before changing HTTPS generation or certificate/auth plugins. [Primary advisory](https://github.com/advisories/GHSA-86w9-cpqp-85rv); [detailed source reachability and patch verification](node-forge-reachability.md). |
| `@octokit/plugin-paginate-rest` 6.1.2; moderate | `paginate.iterator()` processes a malicious response `Link` header with excessive regex backtracking. The old branch is retained by `@octokit/rest` 19.0.13 under Backstage integration/backend defaults. Configured catalog locations are local files; there is no configured GitHub integration or GitHub provider. Thus the reviewed configuration does not invoke this pagination workflow. Fixed branches begin at 9.2.2/11.4.1; forcing those majors into the older Octokit stack requires a coordinated dependency migration. Reassess for GitHub/enterprise integrations or custom request hooks. [Primary advisory](https://github.com/advisories/GHSA-h5c3-5r3r-rr8q). |
| `@octokit/request` 6.2.8; moderate | Response-header parsing in `fetchWrapper` can consume excessive CPU. It remains through `@octokit/auth-app` 4.0.13 under Backstage integration. No GitHub App credentials/provider/integration are configured. Patched branches are 8.4.1/9.2.1; there is no compatible 6.x patch listed. Treat the installed package as affected, and migrate the Octokit auth stack before enabling that integration. [Maintainer advisory](https://github.com/octokit/request.js/security/advisories/GHSA-rmvr-2pp2-xj38). |
| `@octokit/request-error` 3.0.3; moderate | Authorization-header redaction in error construction can backtrack excessively on crafted request headers. The same unconfigured GitHub App stack retains this package. No local API accepts caller-supplied headers and passes them into an Octokit auth request. Fixes start at 5.1.1/6.1.7, requiring the same coordinated major migration. The dependency remains affected even though that workflow is not configured. [Primary advisory](https://github.com/advisories/GHSA-xx4v-prfh-6cgc). |
| `react-router` 6.30.6; moderate | Attacker-controlled paths passed to `Link`/`useNavigate` may navigate to an external origin using backslashes. The installed unpatched code reproduced a cross-origin history failure/fallback when passed mixed separators. The local upstream backport normalizes those paths before navigation; all five regression tests pass, including real rendered hrefs and `useNavigate`. Fixed upstream version 7.18.0 would require moving Backstage routing peers together from major 6; the backport mitigates the documented condition without that migration. Keep the patch until upstream dependency migration incorporates it. [Primary advisory](https://github.com/advisories/GHSA-wrjc-x8rr-h8h6). |
| `react-router` 6.30.6; moderate | `deserializeErrors()` can invoke constructors during SSR hydration. The maintainer limits this to Framework/Data-mode SSR hydration. This app's `packages/app/src/index.tsx` uses `ReactDOM.createRoot(...).render(...)`; Backstage serves a static SPA and does not configure server hydration. The affected workflow is absent in the reviewed app. The package remains affected; reassess before introducing SSR/hydration. Fix is 7.18.0, with the same routing migration. [Primary advisory](https://github.com/advisories/GHSA-337j-9hxr-rhxg). |
| `uuid` 3.4.0, 8.3.2, 9.0.1; moderate | Versions of `v3`/`v5`/`v6` fail to validate caller-supplied output buffers and offsets. Inspected installed callers in Material Table, Google GAX, Gaxios, Teeny Request and SockJS use `v4` without output buffers; that operation is outside this advisory. Cloud integration libraries also retain older copies. This is a bounded call-site review, not proof about every plugin. Fixed branches start at 11.1.1/12.0.1/13.0.1; blindly replacing legacy default-import consumers with major 11 can break them. Migrate dependent packages and reassess any new `v3`/`v5`/`v6` buffer use. [Primary advisory](https://github.com/advisories/GHSA-w5hq-g745-h8pq). |
| `elliptic` 6.6.1; low | Faulty deterministic ECDSA nonce/signature generation can expose a private key under the advisory's conditions. No patched version is listed. The package is retained by browser crypto polyfills (`browserify-sign`/`create-ecdh`) in the CLI's browser dependency graph. The app source implements no browser ECDSA signing or private-key handling; backend JWT authentication uses Node/`jose`. This does not prove every future plugin avoids the polyfill. Reassess before browser cryptographic signing and replace the polyfill chain when upstream offers a supported fix. [Primary advisory](https://github.com/advisories/GHSA-848j-6mx2-7j84). |

## Deprecated packages

The 17 notices cover old ESLint configuration helpers, Material UI 4 components,
`@react-hookz/deep-equal`, `atlassian-openapi`, `eslint`, `glob`, `inflight`,
`lodash.get`, `prebuild-install`, `prom-client`, `react-beautiful-dnd`, `rimraf`,
and `stable`. The JSON preserves each notice and installed version. Deprecation
can identify real maintenance risks, including `inflight`'s leak warning, but it
is not interchangeable with a published vulnerability advisory. Migrating these
packages requires upstream component/tooling changes; deleting portal features
would not be an appropriate remediation.

## Reproduce

Use the Node 22 or 24 runtime required by `backstage/app/package.json`. Yarn is
vendored; no global installation is needed. From the repository root:

```sh
cd backstage/app
node .yarn/releases/yarn-4.4.1.cjs install --immutable
node .yarn/releases/yarn-4.4.1.cjs npm audit --all --recursive --json
node --test scripts/security-router.test.cjs
node .yarn/releases/yarn-4.4.1.cjs workspace backend build
node .yarn/releases/yarn-4.4.1.cjs workspace app build
```

The audit output is newline-delimited JSON. Records with a `children.URL` are
advisories; records whose ID ends with `(deprecation)` are notices. Count these
separately. Re-run `npm audit --json` in `ui` and
`examples/todo-app/frontend-astro`. The final Python summary covers the runtime
requirements, excluding the audit tooling itself.

```sh
uv export --no-dev --no-emit-project --format requirements-txt --no-hashes > /tmp/apim-security-runtime-reqs.txt
pip-audit -r /tmp/apim-security-runtime-reqs.txt --no-deps --disable-pip --format json
```

The Python scan uses the exported runtime graph; `--no-deps --disable-pip`
prevents the scanner from resolving a different graph. No advisory ignore list
was used.

Dependency versions are not reachability controls: changed providers, plugins,
SSR, navigation inputs or cryptographic usage require re-review. The remaining
high advisory has a documented local backport and reachability assessment rather
than a published dependency fix. The documented React Router navigation condition is
locally mitigated and
regression-tested; its installed version remains flagged until an upstream
major migration incorporates the fix.
