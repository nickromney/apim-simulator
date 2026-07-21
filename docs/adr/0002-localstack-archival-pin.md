# ADR 0002: Pin the archived LocalStack community image for the AWS comparison overlay

- Status: accepted
- Date: 2026-07-17
- Supplements: [ADR 0001](0001-goldilocks-ai-gateway-scope.md) decision D4

## Context

Hours after the `compose.aws.yml` overlay shipped, we learned that the
`localstack/localstack` GitHub repository was archived on 2026-03-23. LocalStack
consolidated into a single "unified" image that requires an account-linked auth
token even on the free non-commercial Hobby plan, and the community edition
stopped receiving updates. The `localstack/localstack:4.9` image this repo
pins is therefore the frozen tail of the community line (an October 2025
build): still pullable, still Apache-2.0, verified working against our smoke
test, but it will never see another patch.

That raises the question this ADR answers: is the pin still the right base,
and is there a logical successor?

## Options considered

1. **Unified LocalStack image (current vendor path).** Rejected as the
   default: it prompts for an auth token on start, which breaks this repo's
   clone-and-run property and quietly adds a licensing decision for anyone
   who copies the overlay into a commercial context. Documented as an
   alternative for users who already hold a token.
2. **moto server (`motoserver/moto`).** The obvious open-source successor on
   paper — Apache-2.0, actively maintained, and the engine LocalStack
   community historically wrapped for many services. **Ruled out
   empirically**: moto's control plane accepted our full REST API setup
   (create-rest-api, `{proxy+}` resource, `HTTP_PROXY` integration,
   deployment), but moto server has no REST data plane. Its host dispatcher
   maps `*.execute-api.*.amazonaws.com` to `apigatewaymanagementapi` (the
   WebSocket management service) and nothing serves gateway invocations, so
   requests fall through to the S3 backend and 404. Moto's "HTTP integration"
   support is in-process test mocking, not a network gateway. Without a data
   plane there is no side-by-side comparison to demo.
3. **Post-archival community successors (LocalEmu, MiniStack, Floci, kumo,
   fakecloud).** All launched in the weeks around the archival. Too young to
   pin a teaching repo to; none has yet demonstrated durable maintenance or
   API Gateway data-plane coverage. Watch list, not a dependency.
4. **Keep the frozen `localstack/localstack:4.9` pin.** Works today
   (verified by `make smoke-aws`), account-free, Apache-2.0, and confined to
   an opt-in overlay that is deliberately outside `up-all` and CI.

## Decision

Keep the `localstack/localstack:4.9` pin, with eyes open:

- The overlay is a comparison aid, not infrastructure. A frozen emulator that
  correctly serves REST API v1 `HTTP_PROXY` invocation is fit for that
  purpose indefinitely; the AWS API Gateway v1 surface it emulates is itself
  stable.
- The overlay stays opt-in (not in `up-all`, not in CI), so the unmaintained
  image never sits in anyone's default path.
- `docs/MIGRATING-FROM-AWS-API-GATEWAY.md` notes the archival, this ADR, and
  the unified-image alternative for token holders.

## Revisit triggers

Re-evaluate (in order of preference: a proven OSS successor, then the unified
image) if any of these occur:

- the `4.9` image becomes unpullable from Docker Hub;
- a successor project demonstrates six months of maintenance plus working
  REST API data-plane emulation;
- moto grows a real execute-api data plane in server mode;
- this repo's AWS comparison needs grow beyond REST v1 proxy integration
  (e.g. authorizers, HTTP APIs v2), which the frozen image may cover poorly.
