# 4 - Protect your API

Microsoft Learn equivalent tutorial: [Tutorial: Transform and protect your API](https://learn.microsoft.com/en-us/azure/api-management/transform-api)

Add a response header and limit calls by subscription.

## Apply the policy

From the repo root:

```bash
export APIM_BASE=http://localhost:8000
export APIM_TENANT_KEY=local-dev-tenant-key
```

These commands assume `tutorial-api` and `tutorial-key` already exist. If not,
run steps 1 and 2 first or use `./docs/tutorials/apim-get-started/tutorial02.sh --setup`.

Apply an API policy that adds a custom header and rate-limits by subscription:

```bash
curl -sS -X PUT -H "X-Apim-Tenant-Key: $APIM_TENANT_KEY" \
  -H "Content-Type: application/json" \
  "$APIM_BASE/apim/management/policies/api/tutorial-api" \
  --data '{"xml":"<policies><inbound><rate-limit-by-key calls=\"3\" renewal-period=\"15\" counter-key=\"@(context.Subscription.Id)\" /><base /></inbound><backend><base /></backend><outbound><set-header name=\"Custom\" exists-action=\"override\"><value>My custom value</value></set-header><base /></outbound><on-error><base /></on-error></policies>"}'
```

Verify the transform and throttling:

```bash
curl -i -H "Ocp-Apim-Subscription-Key: tutorial-key" "$APIM_BASE/tutorial-api/health"
curl -i -H "Ocp-Apim-Subscription-Key: tutorial-key" "$APIM_BASE/tutorial-api/health"
curl -i -H "Ocp-Apim-Subscription-Key: tutorial-key" "$APIM_BASE/tutorial-api/health"
curl -i -H "Ocp-Apim-Subscription-Key: tutorial-key" "$APIM_BASE/tutorial-api/health"
sleep 16
curl -i -H "Ocp-Apim-Subscription-Key: tutorial-key" "$APIM_BASE/tutorial-api/health"
```

The first three calls return `200` with `Custom: My custom value`. The fourth
returns `429` with a `Retry-After` header. After 16 seconds, the same key succeeds
again with the custom header. The scripted verification checks that complete
sequence, including renewal; allow at least 16 seconds for `--verify`.

## Run the scripted exercise

To set up and verify the exercise with a script, run:

```bash
./docs/tutorials/apim-get-started/tutorial04.sh --setup
./docs/tutorials/apim-get-started/tutorial04.sh --verify
```

Use `--setup` to import the API and apply the policy. Use `--verify` to check
the header and rate limit on the running stack.
