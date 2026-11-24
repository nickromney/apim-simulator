# Retry policy — adapted subset

`<retry>` runs its child policies once, then evaluates `condition`. While the
condition is true and retries remain, it waits and runs the children again.
It is accepted in inbound, backend, outbound and on-error, at every scope.

```xml
<retry condition="@(context.Response.StatusCode == 503)" count="3" interval="1"
       first-fast-retry="true">
  <forward-request buffer-request-body="true" />
</retry>
```

| Attribute | Required | Local behaviour |
| --- | --- | --- |
| `condition` | Yes | `@(...)`/`@{...}` expression or `true`/`false`. Evaluated after each run of the children |
| `count` | Yes | Integer retries, 1 to 50. Outside that range the request fails with 500 |
| `interval` | Yes | Seconds between retries (fixed interval) |
| `delta` | No | With `interval`: linear wait, `interval + (n - 1) * delta` |
| `max-interval` | No | With `interval` and `delta`: exponential wait, `interval + 2^(n - 1) * random(0.8 delta, 1.2 delta)`, capped at `max-interval` |
| `first-fast-retry` | No | `true` makes the first retry immediate. Default `false` |

All attributes accept policy expressions. `n` is the retry number, starting
at 1. Microsoft's page gives the exponential formula and a worked example
(10, 20, 40, 80 seconds) that do not agree. The simulator follows the formula.

## Around forward-request

The gateway forwards after the backend section, so a retry that wraps
`forward-request` is completed by the request pipeline. After each forward it
evaluates the condition against the last response's status code and headers.
If the condition holds, it waits, re-runs the retry's children and chooses the
backend again, so `set-variable` and `set-backend-service` can switch to
another backend, then forwards again. This is Microsoft's "switch backend
when error received" example; `tests/test_policy_retry.py` runs it.

Differences from APIM:

- The condition cannot read the response body (`context.Response.Body`) when
  the retry wraps `forward-request`.
- A transport failure, such as a refused connection, ends retrying and takes
  the backend-unavailable path.
- `return-response` inside a retry that wraps `forward-request` is rejected
  when the policy is loaded, as is a second nested retry around the forward.
- `wait` is not implemented, so the documented "no `wait` child" rule is never
  reached.

The request trace records a `retry` step for each retry, with its delay.

Reference: [Microsoft retry policy](https://learn.microsoft.com/en-us/azure/api-management/retry-policy).
Evidence: `tests/test_policy_retry.py` and `tests/test_regional_gateways_example.py` (`POLICY-RETRY`).
