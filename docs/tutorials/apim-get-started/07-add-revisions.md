# 7 - Add Revisions

Source: [Tutorial: Use revisions](https://learn.microsoft.com/en-us/azure/api-management/api-management-get-started-revise-api)

Simulator status: Supported for local revision isolation, routing, and release promotion.

## Run It Locally

From the repo root:

```bash
export APIM_BASE=http://localhost:8000
export APIM_TENANT_KEY=local-dev-tenant-key
./docs/tutorials/apim-get-started/tutorial07.sh --setup
./docs/tutorials/apim-get-started/tutorial07.sh --verify
```

Setup recreates the tutorial gateway and imports the local mock API. It then
rehearses the tutorial's sequence:

1. Keep revision 1 current and online.
2. Copy its definition into revision 2 and add a `POST /test` operation that
   returns `{"sampleField":"revision-two"}`. Existing operations stay in both revisions.
3. Verify the default URL and revision 1 return `404` for the new operation,
   while `tutorial-api;rev=2/test` returns `200`. Revision 1's existing health
   operation still returns `200`.
4. Create the `public` release for revision 2. Verify the default URL now exposes
   the new operation.
5. Take revision 1 offline. Verify its existing health route returns `404`,
   and an unknown revision also returns `404`.

The revision management endpoint accepts a `definition` snapshot containing the
API configuration and its operations. The companion script copies revision 1's
snapshot before editing revision 2, which keeps changes isolated until release.

Create a release to make an existing revision current:

```bash
curl -fsS -X PUT -H "X-Apim-Tenant-Key: $APIM_TENANT_KEY" \
  -H "Content-Type: application/json" \
  "$APIM_BASE/apim/management/apis/tutorial-api/releases/public" \
  --data '{"notes":"Published revision","revision":"2"}'
```

After setup, inspect the current and explicit revision routes:

```bash
curl -i -X POST "$APIM_BASE/tutorial-api/test"
curl -i -X POST "$APIM_BASE/tutorial-api;rev=2/test"
curl -i "$APIM_BASE/tutorial-api;rev=1/health"
curl -i -X POST "$APIM_BASE/tutorial-api;rev=999/test"
```

Expected statuses are `200`, `200`, `404`, and `404`, respectively. The two
successful responses contain `{"sampleField":"revision-two"}`. `--verify` checks
these routes and the current-revision and release metadata without changing state.

## Differences From Azure APIM

- Revision authoring uses the simulator management API and saved definitions
  instead of the Azure portal revision selector.
- The added operation uses an operation policy to return a deterministic sample;
  no change to the mock backend is needed.
- Release notes are exposed in the local developer portal catalog for APIs
  associated with a visible published product. This standalone script checks
  release metadata; it does not publish a product or exercise the portal UI.
