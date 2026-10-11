# Subscription keys

The gateway rejects calls to a subscription-protected product without a valid key. It accepts the key in either `Ocp-Apim-Subscription-Key` or `X-Ocp-Apim-Subscription-Key`, matches Azure's 401 message, and forwards calls that carry a valid key.

## Sub-features

- `sub-missing` returns 401 "Access denied due to missing subscription key…".
- `sub-invalid` returns 401 for an unknown key.
- `sub-valid` returns 200 for the primary or secondary key.

## How to get to it (user POV)

- Call `http://127.0.0.1:8700/api/...` with and without the header.

## Driving it with control.sh

Preconditions:

- `control.sh --execute launch` (the hello-subscription stack) and `doctor` print `OK`.

- **Missing key.** `control.sh --execute request GET /api/health` gives `status=401` and a body that contains `missing subscription key`.
- **Valid key.** `control.sh --execute request GET '/api/hello?name=verify' 'Ocp-Apim-Subscription-Key: hello-demo-key'` gives `status=200` and `{"message":"hello, verify"}`.
- **All three.** `control.sh --execute smoke` prints `missing subscription: 401`, `invalid subscription: 401` and `valid subscription: 200`, with `exit=0`.

## Gotchas

- Health checks are not exempt. `/api/health` also needs a key on this stack.
