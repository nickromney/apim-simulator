# Anonymous hello

The smallest starter: an API behind the gateway with `allow_anonymous` and no subscription requirement.

## Sub-features

- `anon-health` returns 200 for `/api/health` with no headers.
- `anon-hello` returns a greeting from `/api/hello?name=`.

## How to get to it (user POV)

- `make up-hello`, then `curl http://localhost:8000/api/hello?name=you`.

## Driving it with control.sh

Preconditions:

- `control.sh --execute launch hello`.

- **Smoke (not yet driven).** `control.sh --execute smoke` runs `smoke_hello.py` in `anonymous` mode and expects `exit=0`.

## Gotchas

- Verify one stack at a time. Run cleanup before launching a different stack on the same slot.
