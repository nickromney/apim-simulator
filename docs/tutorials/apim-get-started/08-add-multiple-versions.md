# 8 - Add Multiple Versions

Source: [Tutorial: Add multiple versions](https://learn.microsoft.com/en-us/azure/api-management/api-management-get-started-publish-versions)

Simulator status: Supported locally

## Run It Locally

The shortcut follows Microsoft's version creation workflow: create an unversioned API, clone it into a Path version, preserve the source as **Original**, and include both APIs in a published product.

```bash
./docs/tutorials/apim-get-started/tutorial08.sh --setup
./docs/tutorials/apim-get-started/tutorial08.sh --verify
```

Setup creates these local resources:

| Resource | Identifier or URL |
| --- | --- |
| Published product | `version-tutorial-product` |
| Source API, Original | `path-versioned-original`, `/path-versioned/echo` |
| Cloned version v2 | `path-versioned-v2`, `/path-versioned/v2/echo` |
| Version set | `tutorial-path-versions`, Segment scheme (the portal calls this Path) |
| Product subscription | `version-tutorial-sub`, local key `version-tutorial-key` |

The v2 API inherits the source's operations, schemas, policies, and product association. Setup then edits only the cloned echo operation to return `x-version: v2`. The Original API continues working at its previous URL without this header. Verification checks both requests, product catalog visibility, and the existing Header version example.

Set `APIM_BASE`, `APIM_TENANT_KEY`, `PATH_VERSIONED_PATH`, `PATH_VERSION_SET_ID`, `VERSION_PRODUCT_ID`, `VERSION_SUBSCRIPTION_ID`, or `VERSION_SUBSCRIPTION_KEY` to use different local inputs. Setup recreates its two Path API identifiers so it can be repeated; `--verify` performs read-only checks.

## Create a Version Through the Management API

Start with an API containing operations. Publish a product explicitly, then associate it with the API:

```bash
export APIM_BASE=http://localhost:8000
export APIM_TENANT_KEY=local-dev-tenant-key

curl -fsS -X PUT -H "X-Apim-Tenant-Key: $APIM_TENANT_KEY" \
  -H 'Content-Type: application/json' \
  "$APIM_BASE/apim/management/products/version-tutorial-product" \
  --data '{"name":"Version tutorial product","state":"published","require_subscription":true}'

curl -fsS -X PUT -H "X-Apim-Tenant-Key: $APIM_TENANT_KEY" \
  -H 'Content-Type: application/json' \
  "$APIM_BASE/apim/management/apis/path-versioned-original" \
  --data '{"name":"Path versioned API","path":"path-versioned","upstream_base_url":"http://mock-backend:8080/api","products":["version-tutorial-product"]}'

curl -fsS -X PUT -H "X-Apim-Tenant-Key: $APIM_TENANT_KEY" \
  -H 'Content-Type: application/json' \
  "$APIM_BASE/apim/management/apis/path-versioned-original/operations/echo" \
  --data '{"name":"Echo","method":"GET","url_template":"/echo"}'

curl -fsS -X POST -H "X-Apim-Tenant-Key: $APIM_TENANT_KEY" \
  -H 'Content-Type: application/json' \
  "$APIM_BASE/apim/management/apis/path-versioned-original/versions" \
  --data '{"version_id":"path-versioned-v2","api_version":"v2","version_set_id":"tutorial-path-versions","versioning_scheme":"Path"}'
```

The version workflow creates or attaches the version set, snapshots revision 1, and retains the unversioned source as Original. New versions are independently editable. Existing API identifiers, duplicate version labels, and conflicting version-set settings return an error without changing the running configuration. To select different products for the clone, include `"products":["another-product"]`; omit `products` to inherit the source's links.

Request both APIs using a subscription to the product. The shortcut creates the following local subscription key:

```bash
curl -i -H 'Ocp-Apim-Subscription-Key: version-tutorial-key' \
  "$APIM_BASE/path-versioned/echo"
curl -i -H 'Ocp-Apim-Subscription-Key: version-tutorial-key' \
  "$APIM_BASE/path-versioned/v2/echo"
```

The portal catalog at `/apim/portal` lists Original and v2 under the published product. Its operation metadata includes the URL and selector headers required by each version. Exporting a version through `/apim/management/apis/<api-id>/export` produces OpenAPI with the matching public server URL or version selector.

## Other Versioning Schemes

Use `"versioning_scheme":"Header","version_header_name":"x-api-version"` to select a version through a request header, or `"versioning_scheme":"Query","version_query_name":"api-version"` to select it through a query parameter. After a version set exists, omit `versioning_scheme` to reuse its settings. An unversioned Original is reached without a selector.

The shortcut also keeps the Header example:

```bash
curl -i -H 'x-api-version: v1' "$APIM_BASE/versioned/echo"
curl -i -H 'x-api-version: v2' "$APIM_BASE/versioned/echo"
```

Expected verification includes `"version_description": "Original"`, both API identifiers in the product catalog, HTTP 200 for Original and v2, and `"x_version": "v2"` only for the modified version.
