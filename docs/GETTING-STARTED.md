# Getting started: make a call, request access, follow the request

In this journey you will run the local simulator, call an anonymous API, request a subscription, call a protected API, and explain the result using a trace. Allow about 15 minutes after the first Docker build. No Azure account or cloud deployment is needed. This is an independent, unofficial project.

Use the default `make up` fixture for this journey. Other examples have different routes, identities and access rules. Run terminal commands from the repository root. You need Docker running and the default host ports `8000` and `3007` available; the first build downloads images and dependencies. If Docker asks for access to `dhi.io`, see the README's [image setup and public-image alternatives](../README.md#container-hardening).

## 1. Start the local stack

```bash
make up
curl -i http://localhost:8000/apim/health
```

**Checkpoint:** the health request returns `200`. Allow the containers a few seconds to start and retry if the first call cannot connect. If it still fails, check the build output and Docker status before continuing.

There are two browser interfaces and one gateway:

| Address | Use it for |
| --- | --- |
| [localhost:8000](http://localhost:8000/api/echo) | Sending API requests to the gateway |
| [localhost:8000/apim/portal](http://localhost:8000/apim/portal#getting-started) | Discovering APIs and obtaining a consumer subscription key |
| [localhost:3007](http://localhost:3007) | Managing APIs and inspecting traces as an operator |

The portal is a page served by the gateway. The console is a separate application. Both help you work with the same gateway; API traffic goes from the caller to the gateway and then, when allowed, to the backend.

## 2. Make your first API call

```bash
curl -i 'http://localhost:8000/api/echo?lesson=getting-started'
```

**Checkpoint:** `200`, a JSON response with `"ok": true`, method `GET`, and path `/api/echo?lesson=getting-started`. Read the response headers as well as the body.

The mock backend echoes what it received. This route allows anonymous access: you have proved that a request can pass through the gateway to the backend, without supplying a credential.

## 3. Reach the subscription gate

```bash
curl -i http://localhost:8000/demo/echo
```

**Checkpoint:** `401`. This is the expected result for this fixture, not a broken setup. `/demo/echo` reaches the same echo backend, but its product requires a subscription key. The gateway rejects this call before forwarding it.

## 4. Request access in the developer portal

Open [the developer portal](http://localhost:8000/apim/portal#getting-started).

1. In **Portal identity**, keep **Demo Developer (demo-dev)** selected. The status should say **Demo access**. This local fixture does not require sign-in; leave **Portal token** empty.
2. In **Product catalog**, find **API learning demo**, marked **subscription required**. It contains the **Subscription demo** API.
3. Choose **Request subscription** once.
4. In **My subscriptions**, find the new subscription and check its state is **active**. Its **Primary key** is the consumer credential you will use for this product.

**Checkpoint:** an active subscription and a generated primary key are visible. An API is the callable contract; a product groups APIs and gives consumers a way to obtain access to them. This default product grants access immediately. Products in other fixtures may require operator approval and leave requests **submitted**.

## 5. Send the protected call with your key

In **Explore an API**:

1. Choose **Subscription demo** in **API** and **Original** in **Version**.
2. In **Subscription key**, select the subscription you just created, marked **active**.
3. In the embedded Scalar reference, open the **Echo** operation and choose **Test Request**.
4. Check the request is `GET http://localhost:8000/demo/echo`, then choose **Send**.

**Checkpoint:** Scalar shows `200` and the echoed JSON. The backend's echoed path is `/api/echo`: the gateway maps the public `/demo` prefix to the backend's `/api` prefix. Selecting the subscription supplies the `Ocp-Apim-Subscription-Key` header to Scalar.

Use **Quick operation check** → **Send** below the reference if you want the same call in the portal's smaller client.

You have passed a subscription check. That does not establish a human user's identity or permission to edit a particular business record. Those are separate checks, covered in the [API and auth lesson](API-BASICS.md#4-access-keys-identity-and-browser-permission-are-different-checks).

## 6. Change one thing and predict the result

Choose **none** in **Subscription key**, then reopen **Test Request** and send again. Expect `401`. Select your active subscription again, reopen the request client and send: expect `200`.

For an invalid-key case, use:

```bash
curl -i http://localhost:8000/demo/echo \
  -H 'Ocp-Apim-Subscription-Key: invalid-demo-key'
```

**Checkpoint:** missing and invalid keys return `401`; the active key returns `200`; anonymous `/api/echo` still returns `200`. The difference is the route's access contract, not which screen sends the request.

## 7. See what the gateway did

Keep your active subscription selected. In Scalar's request editor for **Echo** (open **Test Request** if needed):

1. In the request editor's **Headers** section, enter `x-apim-trace` in the empty **Key** field and `true` in **Value**. Ensure the row's checkbox is checked.
2. Choose **Send**. Expect `200` again: tracing observes the call without changing its access requirements.
3. In the response, expand **Response Headers** and copy **X-Apim-Trace-Id**. This identifies the portal request you just sent.

**Checkpoint:** the same successful Scalar call has a trace ID. Requests are only traced when you opt in; the earlier calls without that header do not appear in the trace list.

Open [the operator console](http://localhost:3007):

1. Open **Configure connection**, choose **Load Local Demo**, then **Connect**. These settings use the default fixture's local management tenant key, which is separate from your consumer key. If you are already connected, the connection panel is available under **Connected** → **Manage**.
2. Choose **Request traces**. If you connected before sending the request, choose **Refresh** first.
3. In the **Trace Ledger**, select the successful **Subscription demo:Echo** route. Expand **Raw trace JSON** to match the `trace_id` to the response header you copied.
4. Inspect the selected route, upstream URL, response status and policy steps. The upstream should be `http://mock-backend:8080/api/echo` and the forwarding attempt count should be `1` in the raw trace.

You can also read that trace directly:

```bash
curl 'http://localhost:8000/apim/trace/<trace-id>'
```

The missing-key `401` from step 6 does not create a trace entry: the subscription gate runs before policy tracing starts. The basic fixture permits local reads of captured traces. Trace permissions vary in other fixtures, and traces may include demo headers and bodies.

**Checkpoint:** you can explain both outcomes: the allowed call's trace shows the echo backend; the rejected call stopped at the subscription gate before that stage. A status code tells you the result; a captured trace explains the stages the request reached.

## If a checkpoint does not match

| Symptom | Check next |
| --- | --- |
| Connection refused or no health `200` | Docker is running, `make up` completed, and no other stack owns port `8000`. Use the [address guide](LOCAL-ADDRESSES.md) if you chose another stack slot. |
| **API learning demo** is missing | You are using the default fixture and **Demo Developer**. Existing management edits or another example can change the catalog; follow the [backup/reset guide](LOCAL-LIFECYCLE.md) before resetting anything. |
| Key dropdown is empty | The request succeeded, a subscription is listed under the same demo user, and its state is **active**. Reload the portal if needed. |
| Selected key still returns `401` | The selected subscription is active, the route is `/demo/echo`, and the Scalar request includes `Ocp-Apim-Subscription-Key`. Reopen **Test Request** after changing the key. |
| Console connection fails | **Load Local Demo** targets the default `8000` gateway. For another slot, correct **Gateway base URL** before **Connect**. |
| No request trace appears | Send with `x-apim-trace: true`, then refresh the console. Traces are in memory and disappear on a gateway restart. |

## Finish, then choose the next question

The default runtime configuration and your new subscription are temporary. [Save anything you want to keep](LOCAL-LIFECYCLE.md) before stopping or restarting the gateway. To stop the default core stack only:

```bash
docker compose -f compose.yml -f compose.public.yml -f compose.ui.yml down
```

Continue with [API basics](API-BASICS.md) for the request and access model, the [OIDC walkthrough](walkthrough-oidc-gateway.md) for bearer tokens and role checks, or the [starter recipe](APIM-STARTER-RECIPE.md) to put your own backend behind the gateway. Each builds on the request you can now explain.
