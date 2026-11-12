# Practice APIOps locally

Promote an HTTP API from a disposable source gateway to a separate target.
Extract its OpenAPI definition and authored policies, review a change in Git,
and publish the reviewed commit. Verify the live response and roll back.

[Microsoft Learn equivalent tutorial](https://learn.microsoft.com/en-us/azure/architecture/example-scenario/devops/automated-api-deployments-apiops)

## Before you start

Run from the repository root in a fresh Bash shell. Check that Docker Compose
can reach a running Docker Engine and that the other tools are available.

```bash
docker compose version && docker info >/dev/null
command -v make && command -v jq && command -v lsof
curl --help all | grep -- --retry-all-errors
git --version && uv --version
```

The first build downloads images and dependencies. If you need to use public
base images, set the [public image overrides](../README.md#container-hardening)
before launching the gateways.

Choose two unused stack slots. The example uses slot 7 for extraction and slot
8 for publishing. A slot adds 100 times its number to port 8000. Check the ports
and Compose project names before starting. Each `lsof` command should print
nothing, and neither project should appear in the Compose list. Change the
slot values if either check finds a conflict.

```bash
source_slot=7
target_slot=8
source_url="http://127.0.0.1:$((8000 + 100 * source_slot))"
target_url="http://127.0.0.1:$((8000 + 100 * target_slot))"
lsof -nP -iTCP:$((8000 + 100 * source_slot)) -sTCP:LISTEN
lsof -nP -iTCP:$((8000 + 100 * target_slot)) -sTCP:LISTEN
docker compose ls -a
```

## Start the gateways and prepare the source API

```bash
STACK_SLOT="$source_slot" make up-gateway
STACK_SLOT="$target_slot" make up-gateway
curl -fsS --retry 15 --retry-all-errors --retry-delay 1 "$source_url/apim/health"
curl -fsS --retry 15 --retry-all-errors --retry-delay 1 "$target_url/apim/health"
```

Both health requests should succeed. Create a temporary Git repository and
import the example API into the source gateway. It starts with one operation
that forwards to the bundled mock backend. This example uses the demo tenant
key. With signed credentials, use API read access to extract and plan and API
write access to apply.

```bash
repo="$(mktemp -d)"
git -C "$repo" init --initial-branch=main
git -C "$repo" config user.name 'Local tutorial'
git -C "$repo" config user.email tutorial@example.invalid
git -C "$repo" config commit.gpgsign false
unset APIM_BEARER_TOKEN
export APIM_TENANT_KEY=local-dev-tenant-key

cat > "$repo/source-openapi.json" <<'JSON'
{
  "openapi": "3.0.3",
  "info": {"title": "APIOps demo", "version": "1.0"},
  "servers": [{"url": "http://mock-backend:8080/api"}],
  "paths": {
    "/echo": {"get": {"operationId": "echo", "responses": {"200": {"description": "Echo"}}}}
  }
}
JSON

uv run apimsim --base-url "$source_url" import-openapi apiops-demo --file "$repo/source-openapi.json"

cat > "$repo/api-policy.xml" <<'XML'
<policies>
  <outbound>
    <base />
    <set-header name="X-APIOps-API" exists-action="override"><value>active</value></set-header>
  </outbound>
</policies>
XML

cat > "$repo/echo-policy.xml" <<'XML'
<policies>
  <outbound>
    <base />
    <set-header name="X-APIOps-Echo" exists-action="override"><value>baseline</value></set-header>
  </outbound>
</policies>
XML

uv run apimsim --base-url "$source_url" set-policy api apiops-demo --file "$repo/api-policy.xml"
uv run apimsim --base-url "$source_url" set-policy operation apiops-demo:echo --file "$repo/echo-policy.xml"
```

## Extract and publish a baseline

Extract the source API into a JSON file. The file contains an OpenAPI document
with a relative gateway path, the API policy, and the operation policies
keyed by OpenAPI operation ID. Inspect the artifact for secrets before
committing it. In this example the policies set response headers.

```bash
uv run apimsim --base-url "$source_url" extract-api apiops-demo > "$repo/api.json"
jq '.openapi' "$repo/api.json"
jq -r '.api_policy, .operation_policies.echo' "$repo/api.json"
git -C "$repo" add api.json
git -C "$repo" commit -m 'Record baseline API'
```

Plan against the target gateway. The target lacks `apiops-demo`, so supply
its upstream base URL with the mock backend's `/api` prefix. The plan
should show a new API with one operation.

```bash
export APIM_BASE_URL="$target_url"
upstream=http://mock-backend:8080/api
uv run apimsim plan-api apiops-demo --file "$repo/api.json" \
  --upstream-base-url "$upstream" > "$repo/baseline-plan.json"
jq -er '.diff' "$repo/baseline-plan.json"
```

Apply the baseline with the quoted ETag from the target's plan. The request
should return HTTP 200 and include `X-APIOps-Echo: baseline`.

```bash
etag="$(jq -er '.etag' "$repo/baseline-plan.json")"
uv run apimsim apply-api apiops-demo --file "$repo/api.json" \
  --upstream-base-url "$upstream" --if-match "$etag"
curl -fsS -D - -o /dev/null "$target_url/apiops-demo/echo"
```

## Review and record a change in Git

Add a health operation to the OpenAPI document and change the echo response
header in its operation policy.

```bash
git -C "$repo" switch -c api-change
jq '
  .openapi.paths["/health"] = {
    get: {operationId: "health", summary: "Health", parameters: [], responses: {"200": {description: "Healthy"}}}
  }
  | .operation_policies.echo |= sub(">baseline<"; ">published<")
' "$repo/api.json" > "$repo/proposed.json"
mv "$repo/proposed.json" "$repo/api.json"
git -C "$repo" diff --check
git -C "$repo" diff -- api.json
```

Confirm that the diff adds `GET /health` and updates the echo policy. For
team releases, run API tests and applicable contract and security checks in the
pull request. Use the merged commit after approval. In this disposable
repository, merge locally and export the release file from the commit.

```bash
git -C "$repo" add api.json
git -C "$repo" commit -m 'Add health and update the echo policy'
git -C "$repo" switch main
git -C "$repo" merge --ff-only api-change
release="$(git -C "$repo" rev-parse HEAD)"
git -C "$repo" show "$release:api.json" > "$repo/release.json"
printf 'Release commit %s\n' "$release"
```

## Preview and publish to the target

Use the release file for both commands. The target validates the definition
and policies together. Leave the upstream override out to use the backend
already configured on the target API.

```bash
uv run apimsim plan-api apiops-demo --file "$repo/release.json" > "$repo/plan.json"
jq -er '.diff' "$repo/plan.json"
```

Review the target diff, then apply the release file using the ETag from the
plan. The API definition and policies take effect together.

```bash
etag="$(jq -er '.etag' "$repo/plan.json")"
uv run apimsim apply-api apiops-demo --file "$repo/release.json" --if-match "$etag"
```

HTTP 412 means the target API changed after you planned. Review the new
target state and diff before retrying. Run one publisher at a time for each
target. When promoting the same commit to another target, obtain a plan and
ETag from that target.

## Verify the deployment

Compare the deployed definition and policies with the release file. Check
that the health operation works and that both policies run on the echo
request.

```bash
uv run apimsim extract-api apiops-demo | jq -S . > "$repo/deployed.json"
jq -S . "$repo/release.json" > "$repo/expected.json"
diff -u "$repo/expected.json" "$repo/deployed.json"
curl -fsS -D - -o /dev/null "$target_url/apiops-demo/echo"
curl -fsS "$target_url/apiops-demo/health"
```

`diff` should print nothing. Echo should return `X-APIOps-API: active` and
`X-APIOps-Echo: published`. Health should return HTTP 200. Record the release
commit with your test results. If someone makes an approved emergency change
on a gateway, extract the updated artifact and review it in Git.

## Roll back

Revert the release commit and use the same preview and publish sequence.
For a team release, review and approve the revert before publishing it.

```bash
git -C "$repo" revert --no-edit "$release"
git -C "$repo" show HEAD:api.json > "$repo/rollback.json"
uv run apimsim plan-api apiops-demo --file "$repo/rollback.json" > "$repo/rollback-plan.json"
jq -er '.diff' "$repo/rollback-plan.json"
```

The rollback diff should remove the health operation and restore the echo
policy. Apply it and check the response again. Echo should return
`X-APIOps-Echo: baseline`, and health should return HTTP 404.

```bash
etag="$(jq -er '.etag' "$repo/rollback-plan.json")"
uv run apimsim apply-api apiops-demo --file "$repo/rollback.json" --if-match "$etag"
curl -fsS -D - -o /dev/null "$target_url/apiops-demo/echo"
curl -sS -o /dev/null -w '%{http_code}\n' "$target_url/apiops-demo/health"
```

## Clean up

Remove the two disposable Compose projects. Keep the temporary repository at
`$repo` if you want the Git history and deployment plans.

```bash
for slot in "$source_slot" "$target_slot"; do
  APIM_GATEWAY_PORT=$((8000 + 100 * slot)) docker compose \
    --project-name "apim-simulator-core-s$slot" -f compose.yml -f compose.public.yml down
done
```

## Prepare other targets

The simulator promotes one HTTP API at a time. Each target supplies its own
shared policies, products, subscriptions, policy fragments, named values, and
backends. For a protected API, create the target API with its intended product
membership before promoting the artifact. Check the target plan for changes
the OpenAPI importer makes to the definition. An operation absent from the
artifact is removed from the target API. A
`null` API policy or missing operation policy removes the authored policy at
that scope. See [API artifact promotion](FIDELITY-CONTRACTS.md#api-artifact-promotion)
for the supported OpenAPI subset.

## Run the Azure workflow

Use the Microsoft APIOps CLI and a nonproduction Azure API Management instance
for the Azure scenario, including service-wide artifacts, environment
overrides, and a pipeline with workload identity and deployment approvals.
[Microsoft Learn APIOps setup guide](https://learn.microsoft.com/en-us/azure/api-management/how-to-manage-apiops-cli)
