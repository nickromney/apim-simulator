# Get started with the APIM simulator

Start a local gateway, import your first API, and send a request. Then use the
tutorials to publish APIs, apply policies, and configure the developer portal.

## Start the simulator

Run this command from the repository root.

```bash
make up
```

Open the operator console at <http://localhost:3007>, select **Load Local Demo**,
and select **Connect**. Use the developer portal at
<http://localhost:8000/apim/portal> to discover published APIs and try them with
a subscription key. See the [Microsoft Learn quickstart](https://learn.microsoft.com/en-us/azure/api-management/get-started-create-service-instance).

## Choose a tutorial

| Tutorial | What you can do |
| --- | --- |
| [1. Import your first API](01-import-your-first-api.md) | Import Petstore and the tutorial API, then call the gateway. |
| [2. Create and publish a product](02-create-and-publish-a-product.md) | Publish an API in the developer portal and request a subscription. |
| [3. Mock API responses](03-mock-api-responses.md) | Save a response example and return it from the gateway without a backend. |
| [4. Protect your API](04-protect-your-api.md) | Add a response header, apply a rate limit, and check when access resumes. |
| [5. Monitor published APIs](05-monitor-published-apis.md) | Inspect local metrics, logs, traces, and alerts. |
| [6. Debug your APIs](06-debug-your-apis.md) | Trace a request and inspect the policies and backend it used. |
| [7. Add revisions](07-add-revisions.md) | Test a revision and make it current when it is ready. |
| [8. Add multiple versions](08-add-multiple-versions.md) | Create a version and call it alongside the original API. |
| [9. Customize the developer portal](09-customise-developer-portal.md) | Edit branding and pages, preview the draft, and publish it. |
| [10. Work with your API using Bruno CLI](10-manage-apis-in-visual-studio-code.md) | Import, edit, test, and export an API with the Bruno collection. |
| [11. Link to a local API Center](11-link-to-an-api-center.md) | Link a catalog and see source API changes appear in its inventory. |

APIM workspaces are unsupported in the simulator. [Microsoft Learn workspace overview](https://learn.microsoft.com/en-us/azure/api-management/workspaces-overview)

## Run the tutorials from the terminal

Before running the tutorial scripts, [check Docker, curl, and uv](01-import-your-first-api.md#check-prerequisites).

Set the gateway URL and demo tenant key for the terminal examples.

```bash
export APIM_BASE=http://localhost:8000
export APIM_TENANT_KEY=local-dev-tenant-key
```

Use `--setup` to create a tutorial's resources and `--verify` to check the result.
Use `--dry-run` to preview setup. The Bruno lesson reruns authoring requests
during verification, and the debugging lesson issues temporary credentials.

## Run all tutorials in a separate stack

Install `mkcert` and run `mkcert -install` for the monitoring tutorial. Use any
unused positive stack slot. Each slot adds 100 times its number to the default
host ports. For example, slots 2, 6, and 8 use gateway ports 8200, 8600, and 8800.

Replace `2` with your chosen slot.

```bash
make ensure-certs
env -u APIM_BASE STACK_SLOT=2 make -C examples/apim-tutorials smoke
```

The command recreates the tutorial stack between lessons and removes it when
the run finishes.

## Stop the simulator

Save any edits you want to keep before stopping the stack.
[Save and restore your edits](../../LOCAL-LIFECYCLE.md#save-edits-before-stopping)

Run `make down` to stop the default stack. If you ran an individual tutorial
script with a different slot, use the same slot with
[`tutorial-cleanup.sh --execute`](tutorial-cleanup.sh).
