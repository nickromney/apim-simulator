# 11 - Link to a local API Center

Microsoft Learn equivalent tutorial: [Link to an API Center](https://learn.microsoft.com/en-us/azure/api-management/tutorials/link-api-center).

Create an API Center and link it to your simulator. You can then browse API
assets and OpenAPI definitions and see the catalog update when APIs change.

```bash
./docs/tutorials/apim-get-started/tutorial11.sh --setup
./docs/tutorials/apim-get-started/tutorial11.sh --verify
```

The script creates a center and links all APIs with definitions enabled. It adds
an API and operation, checks their definition, edits its title, deletes it, and
checks that each source change propagates. It then unlinks, verifies that synchronized
assets are removed, and relinks. Verification checks the link and inventory against
the source API list and compares current inventory IDs, paths, and counts with the
JSON exports saved during setup. Keep the same `APIM_EXPORT_DIR` for verification;
if the exports are missing, run setup again.

Use these tenant-key-protected local endpoints:

| Action | Method and path |
| --- | --- |
| Create a center | `PUT /apim/management/api-centers/{id}` with `{"name":"Local catalog"}` |
| Link | `PUT /apim/management/api-center/link` with `{"center_id":"local","include_definitions":true}` |
| Inspect link | `GET /apim/management/api-center/link` |
| Browse assets | `GET /apim/management/api-centers/{id}/apis` |
| Unlink | `DELETE /apim/management/api-center/link` |

One center can be linked at a time. Changes to API titles, versions, lifecycle, definitions, and local environment and
deployment metadata appear in the catalog. Unlinking removes the synchronized
assets from the catalog; your APIs remain available.
