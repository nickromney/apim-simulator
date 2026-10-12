# Policy pipeline

Each API carries APIM policy XML (inbound, backend, outbound, on-error). The gateway applies it to proxied calls, so headers, bodies and status codes reflect the policy.

## Sub-features

- `policy-outbound-header` uses `set-header` in outbound to add `x-hello-policy: applied`.
- `policy-forward` uses `forward-request` to reach the upstream hello-api.
- `policy-set` replaces a scope's policy through the management API (gateway stack).

## How to get to it (user POV)

- Make any proxied call and inspect the response headers. Operators edit policies with `apimsim set-policy`.

## Driving it with control.sh

Preconditions:

- The hello-subscription stack is up.

- **Outbound header.** `control.sh --execute request GET '/api/hello?name=verify' 'Ocp-Apim-Subscription-Key: hello-demo-key'`. `response-headers.txt` contains `x-hello-policy: applied`.
- **Replace a policy (not yet driven).** On the gateway stack, run `control.sh --execute cli policy api <id>`, then `control.sh --execute cli set-policy …`. Then make a gateway request that shows the new behaviour, and read the policy back again.

## Gotchas

- Keep example XML in Microsoft's documented element order. The local parser accepts orders that Azure rejects.
