# 3 - Mock API responses

Microsoft Learn equivalent tutorial: [Tutorial: Mock API responses](https://learn.microsoft.com/en-us/azure/api-management/mock-api-responses)

Create a mock response and call the API without a running backend.

## Use the operator console

Start the stack with `make up`, open `http://localhost:3007`, and connect with the local demo credentials.

1. Select **New API**, then **HTTP**. Create an API with ID `mock-only`, path `mock-only`, and upstream URL `http://example.invalid`.
2. Select the API and create a `GET` operation with ID `test-call` and URL template `/test`.
3. Select the operation and open **Settings**. Under **Response example and mocking**, enter status `200`, content type `application/json`, and body `{"sampleField":"test"}`. Select **Save response example**.
4. Select **Enable mocking**. The editor reports that mocking is enabled.
5. Open **Test** and select **Send request**. The response is `200` with `{"sampleField":"test"}`; the placeholder backend is not contacted.

The editor changes a direct operation-level inbound `mock-response` policy. If mocking is conditional or appears in multiple places, edit the policy in **Design** instead. The editor preserves other policies and response metadata. Mocking uses the first non-null example for the matching status and content type.

## Use the management API

From the repo root:

```bash
export APIM_BASE=http://localhost:8000
export APIM_TENANT_KEY=local-dev-tenant-key
```

Create a blank API with a placeholder backend:

```bash
curl -sS -X PUT -H "X-Apim-Tenant-Key: $APIM_TENANT_KEY" \
  -H "Content-Type: application/json" \
  "$APIM_BASE/apim/management/apis/mock-only" \
  --data '{"name":"Mock Only","path":"mock-only","upstream_base_url":"http://example.invalid"}'
```

Add an operation with an authored example response:

```bash
curl -sS -X PUT -H "X-Apim-Tenant-Key: $APIM_TENANT_KEY" \
  -H "Content-Type: application/json" \
  "$APIM_BASE/apim/management/apis/mock-only/operations/test-call" \
  --data '{"name":"Test call","method":"GET","url_template":"/test","responses":[{"status_code":200,"representations":[{"content_type":"application/json","examples":[{"name":"ok","value":{"sampleField":"test"}}]}]}]}'
```

Enable native `mock-response` on the operation:

```bash
curl -sS -X PUT -H "X-Apim-Tenant-Key: $APIM_TENANT_KEY" \
  -H "Content-Type: application/json" \
  "$APIM_BASE/apim/management/policies/operation/mock-only:test-call" \
  --data '{"xml":"<policies><inbound><mock-response status-code=\"200\" content-type=\"application/json\" /></inbound><backend /><outbound /><on-error /></policies>"}'
```

Test the mocked API:

```bash
curl -sS "$APIM_BASE/mock-only/test"
```

## Run the setup script

To run setup and verify the results with the tutorial script:

```bash
./docs/tutorials/apim-get-started/tutorial03.sh --setup
./docs/tutorials/apim-get-started/tutorial03.sh --verify
```

Use `--setup` to create the API and mock. Use `--verify` to check the existing state without restarting the stack.

Verification confirms `GET /mock-only/test` returns `{"sampleField":"test"}`.
