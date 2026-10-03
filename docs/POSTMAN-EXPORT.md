# Local Postman collection export

Export a public API contract as a deterministic
[Postman v2.1 collection](https://schema.postman.com/json/collection/v2.1.0/docs/index.html):

```bash
curl --fail --silent --show-error \
  -H "Authorization: Bearer ${APIM_OPERATOR_TOKEN}" \
  "http://localhost:8000/apim/management/apis/${APIM_API_ID}/export?format=postman" \
  -o collection.json
```

Import `collection.json` into Postman. Set the collection's `base_url` to your
published local gateway address, and set `subscription_key` or `bearer_token`
locally as needed. No Azure CLI or editor extension is required.
If an operation includes an Authorization header and you authenticate only with
a subscription key, disable that header in Postman before sending.
Explicit legacy management configurations can use `X-Apim-Tenant-Key` instead of a signed bearer.
The existing export endpoint defaults to OpenAPI; `format=openapi` is equivalent.

The collection includes separate operations, path variables, literal and dynamic
query selectors, optional query parameters, headers, API version selectors, and
request body modes. Dynamic query placeholders such as `{{kind}}` need values in
your Postman environment. Optional query parameters start disabled. The first
request representation selects the content type. JSON bodies start as `{}`;
other raw bodies and form fields start empty. Fill valid values before sending.
Postman computes Content-Length and multipart boundaries.

API and workspace scoped readers can export only APIs within their grants.
The export excludes backend addresses, policy XML, response examples, body
examples and executable scripts. Authentication values are empty collection
variables. Credential-shaped parameter values and configured credential values
are scrubbed, including secret named values, management keys, subscription keys
and backend credentials. Ordinary public parameter defaults and descriptions
remain contract metadata; keep private data out of these fields.

This is a portable collection file export, without creating a remote Postman
workspace or sending credentials to an external service.
