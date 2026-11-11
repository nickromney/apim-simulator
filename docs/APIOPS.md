# Practice APIOps locally

Promote an API policy from a disposable source gateway to a separate target.
Use Git to review the change, preview and publish the reviewed commit, then
verify the live response and roll back.

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

## Start the gateways and extract a policy

```bash
STACK_SLOT="$source_slot" make up-gateway
STACK_SLOT="$target_slot" make up-gateway
curl -fsS --retry 15 --retry-all-errors --retry-delay 1 "$source_url/apim/health"
curl -fsS --retry 15 --retry-all-errors --retry-delay 1 "$target_url/apim/health"
```

Both health requests should succeed. Create a temporary Git repository and
seed both gateways with the same known-good policy for the `default` API.
The seeded policy inherits the parent policy in each section.

```bash
repo="$(mktemp -d)"
git -C "$repo" init --initial-branch=main
git -C "$repo" config user.name 'Local tutorial'
git -C "$repo" config user.email tutorial@example.invalid
git -C "$repo" config commit.gpgsign false
unset APIM_BEARER_TOKEN
export APIM_TENANT_KEY=local-dev-tenant-key

cat > "$repo/policy.xml" <<'XML'
<policies>
  <inbound><base /></inbound>
  <backend><base /></backend>
  <outbound><base /></outbound>
  <on-error><base /></on-error>
</policies>
XML

uv run apimsim --base-url "$source_url" set-policy api default --file "$repo/policy.xml"
uv run apimsim --base-url "$target_url" set-policy api default --file "$repo/policy.xml"
uv run apimsim --base-url "$source_url" policy api default | jq -er '.xml' > "$repo/policy.xml"
git -C "$repo" add policy.xml
git -C "$repo" commit -m 'Record baseline policy'
git -C "$repo" switch -c policy-change
```

The first commit is your rollback input. Review extracted files for secrets and
environment-specific values before committing policies from a real gateway.

## Review and record the change in Git

Add a response header to the outbound policy.

```bash
cat > "$repo/policy.xml" <<'XML'
<policies>
  <inbound><base /></inbound>
  <backend><base /></backend>
  <outbound>
    <base />
    <set-header name="X-APIOps-Demo" exists-action="override"><value>published</value></set-header>
  </outbound>
  <on-error><base /></on-error>
</policies>
XML

git -C "$repo" diff --check
git -C "$repo" diff -- policy.xml
```

Review the diff and confirm that the change adds the expected header. For team
releases, run the API tests and applicable contract and security checks in the
pull request, and use the merged commit after approval. In this disposable
repository, merge locally and export the release file from that commit.

```bash
git -C "$repo" add policy.xml
git -C "$repo" commit -m 'Add demo response header'
git -C "$repo" switch main
git -C "$repo" merge --ff-only policy-change
release="$(git -C "$repo" rev-parse HEAD)"
git -C "$repo" show "$release:policy.xml" > "$repo/release.xml"
printf 'Release commit %s\n' "$release"
```

## Preview and publish to the target

Use the exported XML for both commands. The target checks the proposed XML
against its current configuration and returns a diff and an ETag.

```bash
export APIM_BASE_URL="$target_url"
uv run apimsim plan-policy default --file "$repo/release.xml" > "$repo/plan.json"
jq -er '.diff' "$repo/plan.json"
```

The diff should add the outbound response header. After review, apply the
release file using the ETag from the plan.

```bash
policy_etag="$(jq -er '.etag' "$repo/plan.json")"
uv run apimsim apply-policy default --file "$repo/release.xml" --if-match "$policy_etag"
```

HTTP 412 means the authored policy changed after you planned. Review the new
target state and diff before retrying. Run one publisher at a time for each
target. When promoting the same commit to another target, obtain a plan and
ETag from that target.

## Verify the deployment

Compare the deployed policy with the release file. Call the target API and
check that it returns the new response header.

```bash
uv run apimsim policy api default | jq -er '.xml' > "$repo/deployed.xml"
cmp "$repo/release.xml" "$repo/deployed.xml"
curl -fsS -D - -o /dev/null "$target_url/api/echo" | grep -i '^X-APIOps-Demo: published'
curl -fsS -D - -o /dev/null "$source_url/api/echo"
```

`cmp` should print nothing. The header should appear on the target response.
Record the release commit with your test results. If someone makes an approved
emergency change on a gateway, extract the updated policy and review it in Git
so the next deployment includes the change.

## Roll back

Revert the release commit and use the same preview and publish sequence.
For a team release, review and approve the revert before publishing it.

```bash
git -C "$repo" revert --no-edit "$release"
git -C "$repo" show HEAD:policy.xml > "$repo/rollback.xml"
uv run apimsim plan-policy default --file "$repo/rollback.xml" > "$repo/rollback-plan.json"
jq -er '.diff' "$repo/rollback-plan.json"
```

The rollback diff should remove the response header. Apply it and check the
response again. The header should be absent.

```bash
policy_etag="$(jq -er '.etag' "$repo/rollback-plan.json")"
uv run apimsim apply-policy default --file "$repo/rollback.xml" --if-match "$policy_etag"
curl -fsS -D - -o /dev/null "$target_url/api/echo"
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

## Run the Azure workflow

This exercise covers the lifecycle of one API policy. Use the Microsoft APIOps
CLI and a nonproduction Azure API Management instance to rehearse the full
scenario, including artifact extraction and publishing, resource deletion
plans, environment overrides, and a pipeline with workload identity and
deployment approvals. [Microsoft Learn APIOps setup guide](https://learn.microsoft.com/en-us/azure/api-management/how-to-manage-apiops-cli)
