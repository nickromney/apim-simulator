# Advanced request throttling validation

Reference: [Microsoft Learn advanced request throttling](https://learn.microsoft.com/en-us/azure/api-management/api-management-sample-flexible-throttling).
Exact source snippets and provenance are in `inventory.json` and
`tests/fixtures/policy_guides/api-management-sample-flexible-throttling/`.

All four extracted examples execute locally. The simulator can select the
classic sliding window or the v2 token bucket described by the guide:

```json
{"throttling": {"algorithm": "token-bucket"}}
```

The default is `sliding-window`. For the guide's six-call, sixty-second bucket,
the token bucket allows six immediate calls, rejects the seventh with a
ten-second retry delay, and permits one additional call after ten seconds.
The sliding window permits another call when the oldest call expires instead.
Injected-clock tests verify both behaviors without waiting a minute.

Confirmed gaps fixed during validation:

- `quota-by-key` rejected `bandwidth`. It now accepts a bandwidth limit with or
  without a call limit and accounts for request and response bodies after the
  response is available. Each request contributes rounded-up kilobytes once
  per counter key, including when the same key appears at multiple scopes.
  Response-based increment conditions and quota renewal are tested.
- `AsJwt()?.Subject` failed expression evaluation. The expression adapter now
  reads JWT subjects with null-safe access. JWT inspection alone does not
  authenticate a token; authentication remains a separate policy.
- The guide's `request.Headers` alias was unavailable. It now resolves to the
  request context.
- The v2 token-bucket example previously used the classic sliding algorithm.
  Local configuration now selects either algorithm for subscription and
  keyed rate policies, including deferred increments.

The JWT and client-header source snippets contain nested unescaped XML
attribute quotes. Execution changes the outer attribute quote to a single
quote, preserving the documented expressions. The source fixtures remain
unchanged. Live checks use unique counter keys, subjects, customers, and
reserved test addresses to avoid consuming an earlier run's counters.

`examples/apim-policies/throttling.py` authors six isolated API scenarios through
the management API, calls their gateway routes, and deletes its resources.
The live HTTP run verified:

| Scenario | Result |
| --- | --- |
| Six-call bucket | Six HTTP 200 responses, then 429 with Retry-After |
| IP example | Ten HTTP 200 responses, then 429; another address receives 200 |
| JWT subject example | Ten HTTP 200 responses, then 429; another subject receives 200 |
| Client-header example | One hundred HTTP 200 responses, then 429; another customer receives 200 |
| Bandwidth exhaustion | First body-bearing request receives 200; next receives 403; another address receives 200 |
| Combined product and customer limits | Two calls for one user and one for another consume their shared subscription allowance; the next receives 429 |

Bandwidth exhaustion uses a smaller limit than the source's monthly example
so a live check can exhaust it with a small body. The exact monthly source
example is separately exercised unchanged by the regression suite. Counters
belong to the local gateway process; these checks use one local gateway.

Run the full local guide lab through its Makefile. For a gateway already
running, set `APIM_BASE` and `APIM_TENANT_KEY` and invoke
`uv run --extra dev python examples/apim-policies/throttling.py`.
Focused regression tests are in `tests/test_advanced_throttling_guide.py` and
`tests/test_policy_throttling.py`.
