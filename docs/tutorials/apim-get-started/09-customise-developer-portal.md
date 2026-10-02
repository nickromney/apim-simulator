# 9 - Customise Developer Portal

Source: [Tutorial: Access and customise the developer portal](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-developer-portal-customize)

The local portal supports the tutorial's administrator, customization, preview,
publication, and visitor workflows. Draft content and uploaded images remain
private until publication. The editor uses forms for site settings, shared
branding and navigation, editable pages, and a media library.

## Start the local tutorial

From the repository root:

```bash
export APIM_BASE=http://localhost:8000
export APIM_TENANT_KEY=local-dev-tenant-key
./docs/tutorials/apim-get-started/tutorial09.sh --setup
./docs/tutorials/apim-get-started/tutorial09.sh --verify
```

Setup starts the local stack, publishes an approval-gated product, and creates
its API. It then uploads a PNG, edits branding and pages, previews the draft,
proves visitors cannot see the draft, and publishes it. Finally, it requests a
subscription, checks that its pending key receives HTTP 401, approves it, and
calls the API with the active key. `--verify` checks the published state and
approved subscription without restarting the stack.

The script publishes a baseline portal before editing so repeated runs still
prove that draft changes and newly uploaded images remain unpublished.

## Customize and publish in the browser

1. Open [the portal editor](http://localhost:8000/apim/portal/editor), enter the
   tenant key, and select **Open saved draft**. This corresponds to opening the
   managed developer portal as its administrator.
2. In **Media library**, upload a PNG, JPEG, or WebP image. Each image can be up
   to 2 MiB; the library holds ten images. Choose it under **Saved images** and
   select **Use as logo** or **Use as background**. You can also enter an
   HTTP(S) image URL in the corresponding field.
3. Change **Site title**, **Primary color**, **Background color**, and **Theme**.
   Branding and navigation apply to the home page and other pages.
4. Under **Pages and navigation**, edit a page's heading and text. Enter a new
   address such as `getting-started` and select **Add page**. Pages become links
   in the shared navigation. Page text is escaped as plain text.
5. Select **Save draft**, then **Preview saved draft**. Preview runs in the
   editor and includes privately uploaded images. Preview and publish use the
   saved draft; save any further form changes first.
6. Open [the visitor portal](http://localhost:8000/apim/portal) in a separate
   browser session. It still displays the preceding publication.
7. Select **Publish saved draft**, then refresh the visitor portal. Its title,
   colors, pages, and images now match the saved draft.

The editor's data and publication endpoints require `X-Apim-Tenant-Key`.
Visitors can read the published portal, its pages, and its media without that
header. Draft and published snapshots persist in the simulator configuration
and survive a restart.

## Discover, subscribe, and try an API

Open [the consumer portal](http://localhost:8000/apim/portal). Choose `demo-dev`
as the acting user. The local identity selector corresponds to a configured
developer; requests identify that user with the `X-Apim-Portal-User` header.

Find **Portal Premium** in the catalog and request a subscription. Its approval
setting creates a `submitted` subscription. In [the operator
console](http://localhost:3007), approve the subscription; refresh the consumer
portal and use its key in **Try it** to call `GET /portal-hello/health`.

For versioned APIs, choose the logical **API**, then **Version** and
**Operation**. Original versions use the unversioned request. The console
places named versions in the path segment, version header, or query parameter
configured by their version set.

To create the product manually, explicitly publish it:

```bash
curl -fsS -X PUT -H "X-Apim-Tenant-Key: $APIM_TENANT_KEY" \
  -H "Content-Type: application/json" \
  "$APIM_BASE/apim/management/products/portal-premium" \
  --data '{"name":"Portal Premium","state":"published","require_subscription":true,"approval_required":true}'
```

New products begin unpublished. Open products and unpublished products are
visible only to the administrators group. Product terms must be accepted
before subscribing, and each developer can request additional subscriptions
up to the product's configured limit.

## API equivalents

| Action | Local endpoint |
| --- | --- |
| Read or save the draft | `GET` / `PUT /apim/portal/editor/draft` |
| Upload an image | `POST /apim/portal/editor/media` with `name`, `content_type`, and `content_base64` |
| Preview a saved page | `GET /apim/portal/editor/preview?slug=home` |
| Publish the saved draft | `POST /apim/portal/editor/publish` |
| Read the published snapshot | `GET /apim/portal/content` |
| View a published page | `GET /apim/portal/pages/getting-started` |
| Fetch a published image | `GET /apim/portal/media/{id}` |

The first four actions require the tenant key. An uploaded image's public URL
returns HTTP 404 until a snapshot containing it is published.
