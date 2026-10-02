# Microsoft Learn tutorial workflows locally

The [eleven tutorial guides](../../docs/tutorials/apim-get-started/README.md)
rehearse API Management behavior using local UI and APIs.

Run every tutorial in an isolated slot:

```bash
STACK_SLOT=7 make -C examples/apim-tutorials smoke
```

Tutorial 10 uses [Bruno CLI](https://docs.usebruno.com/bru-cli/overview) to import
OpenAPI, edit API settings, publish a product, create a subscription, apply a
policy, test the response and throttling, and export a reusable API definition.
It uses the same HTTP management API as the operator console.

Against an already running gateway:

```bash
make -C examples/apim-tutorials bruno APIM_BASE=http://localhost:8000
```

The collection owns only the `bruno-api`, `bruno-product` and
`tutorial10-bruno` demo resources. It resets that subscription on each run.
Set `APIM_TENANT_KEY` and `APIM_UPSTREAM_BASE_URL` for another local stack.
Bruno runs in its default safe sandbox. No extension or Azure CLI emulation is
needed. You can also open `bruno/` in the Bruno desktop app.
