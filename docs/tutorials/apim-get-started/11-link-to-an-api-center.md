# 11 - Link to a local API Center

Source: [Link to an API Center](https://learn.microsoft.com/en-us/azure/api-management/tutorials/link-api-center).

The local API Center provides the tutorial's one-way synchronized catalog. Create
it, link the simulator, inspect API assets and optional OpenAPI definitions, then
change source APIs and see the inventory update.

```bash
./docs/tutorials/apim-get-started/tutorial11.sh --setup
./docs/tutorials/apim-get-started/tutorial11.sh --verify
```

The script creates a center and links all APIs with definitions enabled. It adds
an API and operation, checks their definition, edits its title, deletes it, and
checks that each source change propagates. It then unlinks, verifies that synchronized
assets are removed, and relinks. Verification checks the link and inventory against
the source API list. Existing JSON inventory exports remain available.

Use these tenant-key-protected local endpoints:

| Action | Method and path |
| --- | --- |
| Create a center | `PUT /apim/management/api-centers/{id}` with `{"name":"Local catalog"}` |
| Link | `PUT /apim/management/api-center/link` with `{"center_id":"local","include_definitions":true}` |
| Inspect link | `GET /apim/management/api-center/link` |
| Browse assets | `GET /apim/management/api-centers/{id}/apis` |
| Unlink | `DELETE /apim/management/api-center/link` |

One center can be linked at a time. Source management writes synchronously update
API titles, versions, lifecycle, definitions, and local environment/deployment
metadata. Catalog data persists in simulator configuration. Unlink removes the
synchronized assets; source APIs remain available. This supplies a local catalog
workflow without requiring an Azure account.
