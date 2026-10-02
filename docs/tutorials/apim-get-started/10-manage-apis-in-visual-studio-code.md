# 10 - Work with your API using Bruno CLI

Source: [Manage APIs in Visual Studio Code](https://learn.microsoft.com/en-us/azure/api-management/visual-studio-code-tutorial).

The local equivalent uses Bruno to import an OpenAPI document, edit API settings,
attach a product, author a policy, test gateway requests, and export the definition.
Open the collection in Bruno's desktop app to inspect or edit each request, or run
the CLI:

```bash
./docs/tutorials/apim-get-started/tutorial10.sh --setup
./docs/tutorials/apim-get-started/tutorial10.sh --verify
```

Setup starts the local stack. Verify repeats the collection against that stack.
For an already running simulator:

```bash
make -C examples/apim-tutorials bruno APIM_BASE=http://localhost:8000
```

Override `APIM_TENANT_KEY` and `APIM_UPSTREAM_BASE_URL` when using another local
configuration. The pinned CLI runs in its default safe sandbox.

The [collection](../../../examples/apim-tutorials/bruno/) contains eleven ordered
requests. It owns `bruno-api`, `bruno-product` and `tutorial10-bruno`; rerunning it
replaces the demo subscription and uses a fresh rate-limit counter. Three gateway
calls return `200` with `Custom: My custom value`; the fourth returns `429` with
`Retry-After`. The final request exports reusable OpenAPI 3.0.3. A successful run
reports **11 requests passed, 11 tests passed**.

The API authoring and testing outcomes match the source tutorial using local
HTTP interfaces. No Azure CLI emulation or VS Code extension is required.
