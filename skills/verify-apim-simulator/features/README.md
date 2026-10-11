# APIM simulator verification map

This directory is the maintained source for verifying the user-facing behaviour of the APIM simulator through its published gateway port. Read this index, then use the matching feature file.

## Baseline preconditions

- `control.sh --execute launch [stack]` printed `ready` and `control.sh --execute doctor` prints `OK`. The gateway is at `http://127.0.0.1:8700` (slot 7).
- Never drive the slot-0 stack (`make up`, port 8000) or a stack this run did not start.

## Driving conventions

- Send every request through `control.sh --execute request` or `control.sh --execute smoke`, so it is recorded.
- Pass headers as literal `Name: value` arguments. Keys come from the checked-in example configs, never from Azure.
- Use `control.sh --execute cli` only with the `gateway` stack.

## Proof and skip reporting

- HTTP proof is the request, the status, the response headers and the body.
- Pair positive and negative cases, for example 401 without a key and 200 with one.
- Mutation proof is a CLI read-back plus a gateway request that exercises the change.
- Name stacks that were not launched (OTEL, OIDC, MCP, edge/TLS, todo, AI). Don't count them as verified.

## Feature entry contract

Each feature file has an H1, one paragraph, and then `Sub-features`, `How to get to it (user POV)`, `Driving it with control.sh` and `Gotchas`.

## Features

- [Subscription keys](./subscription-keys.md): products that require `Ocp-Apim-Subscription-Key`. **Driven 2026-10-10.**
- [Policy pipeline](./policy-pipeline.md): inbound and outbound policy XML applied to proxied calls. **Outbound header driven 2026-10-10.**
- [Management API and CLI](./management-cli.md): tenant-key management reads and writes. **Reads driven 2026-10-10.**
- [Anonymous hello](./anonymous-hello.md): an API with no subscription. Not yet driven.
