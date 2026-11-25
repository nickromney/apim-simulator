# Plan: keyless APIM → AI Foundry with managed identity

Status: Phase 1 implemented on branch `feat/workload-identity-jwks` (2026-11-25); Phases 2–4 not started.

## Goal

Reproduce the recommended Azure setup locally: APIM calls an AI Foundry /
Azure OpenAI backend using its managed identity rather than an `api-key`. The
Foundry side checks that identity's role assignments. When it works, a
reader can:

1. Run the AI gateway example with no Foundry key anywhere in the APIM config.
2. Watch a chat completion succeed through APIM.
3. Remove the identity's role assignment on the Foundry side and get a 401
   back, as Azure returns when the role is missing.
4. Turn off local (key) auth on Foundry and see direct `api-key` calls
   rejected while calls through APIM still succeed.

## What already exists

In apim-simulator:

- `authentication-managed-identity`, standalone or nested in `send-request`,
  plus backend `auth_type: managed_identity` (`app/policy.py`,
  `app/backend_pool.py`).
- `workload_identity.mode: "signed"` issues an RS256 JWT with `iss`,
  `aud` (= resource), `sub` (= identity), `iat`, `nbf`, `exp`. Identity →
  audience permission comes from `audience_grants` (`app/workload_identity.py`).
- The gateway joins the `aifoundry` Docker network in
  `compose.ai-foundry.yml`.

In aifoundry-simulator:

- `app/auth.py` treats any `Authorization: Bearer <x>` as an API key and checks
  it against `auth.api_keys`. A JWT is always rejected.
- No JWT library in its dependencies.

## Key decision: how Foundry gets APIM's public key

The two stacks run as separate compose projects from separate checkouts. They
share only the `aifoundry` Docker network. Mounting a key file across two repos
means hard-coding a path from one checkout into the other.

**Chosen: APIM publishes a JWKS for its workload identity issuer, and Foundry
fetches it over the shared network.** Real Entra works this way: the resource
trusts an issuer and discovers its keys. Foundry also accepts a static
`public_key_file` so its unit tests and standalone runs don't need APIM.

ADR 0001 D3 rejected a fake IMDS / Entra token endpoint. This plan stays within
that: APIM still mints the token internally. The only new endpoint publishes
verification keys.

## Phase 1: apim-simulator publishes its issuer keys

Changes:

- Add `kid` to the header of tokens issued by `issue_workload_token` (an
  RFC 7638 thumbprint of the public key).
- Serve `GET /.well-known/workload-identity/openid-configuration` (`issuer`,
  `jwks_uri`) and `GET /.well-known/workload-identity/jwks.json`, but only when
  `workload_identity.mode == "signed"` and a signing key is configured.
  Otherwise return 404. (As built: the published key is derived from the
  private signing key; `public_key_file`, if set, is published as an extra
  key to cover rotation.)
- Optional, cheap Entra realism: add `oid` (= identity), `appid` (= client id
  or `system-assigned`) and `tid` (a fixed local tenant placeholder) claims.
  Leave `sub` unchanged.
- Allow `workload_identity.issuer` to be set per stack, so the `iss` claim
  matches the URL Foundry uses to reach APIM (e.g.
  `http://apim-simulator:8000/.well-known/workload-identity`).

Tests (`uv run --extra dev pytest`):

- A token's `kid` matches a key in the published JWKS, and the token verifies
  against it.
- The endpoints return 404 in demo mode and when no public key is set.
- Existing `audience_grants` denial still returns 500 "token acquisition
  failed".

## Phase 2: aifoundry-simulator validates Entra-style tokens

Config (new `auth.entra` block, default off so existing configs are
unchanged):

```yaml
auth:
  api_keys: [local-foundry-key-alpha]
  disable_local_auth: false        # true = reject api-key / key-as-bearer
  entra:
    enabled: true
    trusted_issuers:
      - issuer: http://apim-simulator:8000/.well-known/workload-identity
        jwks_uri: http://apim-simulator:8000/.well-known/workload-identity/jwks.json
        # or: public_key_file: /run/keys/workload-identity.pub.pem
    audiences:
      - https://cognitiveservices.azure.com
      - https://ai.azure.com
    role_assignments:
      system-assigned: [Cognitive Services OpenAI User]
```

Behaviour:

- If the bearer value parses as a JWT and `entra.enabled`, validate it:
  signature against the trusted issuer's keys, `iss`, `aud` in `audiences`,
  `exp`/`nbf` with a small clock-skew allowance, and RS256 only. Anything
  that isn't a JWT keeps the current key path.
- Authorize the principal (`oid`, falling back to `sub`) against
  `role_assignments`. Map routes to required roles:
  - inference (chat, completions, embeddings, responses):
    `Cognitive Services OpenAI User`, `Cognitive Services OpenAI Contributor`,
    `Cognitive Services User` or `Azure AI User`;
  - Content Safety and Language: `Cognitive Services User`;
  - `/foundry/management` stays on admin keys (management plane, unchanged).
- `disable_local_auth: true` rejects `api-key`, `Ocp-Apim-Subscription-Key`
  and key-as-bearer, mirroring `disableLocalAuth` on the Azure resource.
- Error responses: keep invalid-token and missing-role failures distinct.
  Check the exact status codes and bodies against Microsoft's docs before
  writing them, and record the source in the code comment. As far as we know,
  Azure OpenAI returns 401 for both, with `PermissionDenied` naming the
  missing data action when the role is absent. Where Microsoft doesn't
  document the body, say so, as the Content Safety 403 note already does.
- Cache JWKS with a TTL. On an unknown `kid`, refetch once (key rotation),
  then fail closed.

Dependency: add `PyJWT[crypto]`, pinned the way the repo pins other
dependencies.

Tests (`uv run --extra dev pytest` in that repo), with a keypair generated per
test session:

- valid token + role → 200;
- valid token, no role assignment → 401 `PermissionDenied`;
- wrong `aud`, wrong `iss`, expired, `alg: none`, HS256 signed with the public
  key → 401;
- non-JWT bearer still works as a key; `disable_local_auth` blocks it;
- JWKS rotation: unknown `kid` triggers exactly one refetch.

## Phase 3: wire the example end to end

In apim-simulator `examples/ai-gateway/`:

- Makefile target to generate a dev RSA keypair into a gitignored path and
  mount the private key into the gateway (`APIM_WORKLOAD_IDENTITY_PRIVATE_KEY_FILE`).
- Switch `apim.foundry.json` to `workload_identity.mode: "signed"` with
  `audience_grants: {"system-assigned": ["https://cognitiveservices.azure.com"]}`.
- Replace `<set-header name="api-key">…</set-header>` in both APIs with
  `<authentication-managed-identity resource="https://cognitiveservices.azure.com" />`,
  keeping the documented element order.
- Foundry side: an example config (or overlay) in aifoundry-simulator with the
  `auth.entra` block above pointing at the APIM JWKS URL.
- Bruno requests: a chat call through APIM (200), and a direct call to Foundry
  with no key under `disable_local_auth` (401).
- Extend `make smoke-ai-foundry` to assert the forwarded request carried a
  bearer JWT and no `api-key`. Verify through published localhost ports, per
  AGENTS.md.

## Phase 4: docs

- `docs/CAPABILITY-MATRIX.md`: managed identity rows → "signed local JWT,
  verifiable via the issuer JWKS; validated by aifoundry-simulator". Still
  Partial: no Entra, no IMDS, no token caching semantics.
- `examples/ai-gateway/README.md`: a "Go keyless" section written as
  reader steps and expected results. Put the prerequisite check (Foundry stack
  up, keypair generated) before the commands.
- `examples/policy-fragments/README.md`: link the working example from the
  existing "Key or managed identity" note.
- aifoundry-simulator `docs/FOUNDRY-TERM-MAP.md`: map role assignment and
  `disableLocalAuth` to the new config.

## Sequencing and delivery

1. apim-simulator PR: Phase 1. Independent, and useful on its own for any JWT
   consumer.
2. aifoundry-simulator PR: Phase 2. Testable with a static key file, so it
   doesn't wait on (1).
3. apim-simulator PR: Phases 3 and 4. Needs both merged.

Each PR must pass that repo's `make local-ci` before push.

## Out of scope

- Real Entra tokens, `DefaultAzureCredential`, IMDS or a token endpoint for
  clients.
- User-assigned identity selection beyond the existing static `client-id`
  attribute.
- Azure RBAC scopes (subscription / resource group / account inheritance).
  Role assignment is a flat principal → roles map per Foundry instance.
- APIM-side token caching. Each call mints a fresh token; record this as a
  known difference.

## Open questions

- Should `oid` be a GUID-shaped placeholder rather than the readable
  `system-assigned`? A GUID is more realistic, but makes `role_assignments`
  harder to read.
- Should `disable_local_auth` be on by default in the keyless example, or a
  second step the reader turns on (as in goal 4)? Leaning towards a second
  step: it shows the difference.
