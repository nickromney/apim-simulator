# Local emulator design notes

The [agent operating model](AGENT-SYSTEM.md) connects these lifecycle and fidelity principles to authored state, effective state, execution, and evidence.

**Reviewed 2026-10-03.** This is a design comparison for the local APIM
Simulator, based on Microsoft's [Azure Service Bus emulator overview](https://learn.microsoft.com/en-us/azure/service-bus-messaging/overview-emulator)
(last updated 2026-09-19) and [Azurite local development guide](https://learn.microsoft.com/en-us/azure/storage/common/storage-use-azurite)
(last updated 2025-08-26), plus the repository documentation and Compose setup.
Microsoft's products are reference examples; this repository is an independent,
unofficial project and makes no claim of Microsoft endorsement or APIM parity.

## Comparison

| Concern | Microsoft's emulator guidance | This repository | Design lesson |
| --- | --- | --- | --- |
| Purpose and scope | Both products target local development and tests. Service Bus explicitly excludes production use and describes sequential testing. Azurite implements named Storage services rather than the whole Azure platform. | The project targets local APIM gateway, policy, auth, networking, and selected management workflows. [Scope](SCOPE.md) says it is not full APIM parity. | Keep the product framed as a focused simulator for local learning and iteration. Name supported slices and out-of-scope surfaces. Do not describe it as a drop-in APIM replacement. |
| Compatibility and fidelity | Service Bus lists unsupported protocols and cloud features, plus fixed quotas. Azurite names unsupported Storage services, URL differences, error-message differences, lack of performance guarantees, and preview Table support. | [Fidelity contracts](FIDELITY-CONTRACTS.md) define `supported`, `adapted`, and `unsupported`, require Microsoft references and local tests for named contracts, and distinguish local tests from Azure evidence. The capability matrix and curated fixtures add breadth and comparisons. | Continue tying every compatibility statement to a bounded contract, observable behavior, exclusions, and evidence. Mark live Azure comparison separately from documentation-backed local behavior. |
| Persistence and reset | Service Bus says entities and data disappear after container restart; its startup JSON takes precedence over admin-created changes. Azurite persists files in a workspace and documents cleanup by deleting them and restarting. | The simulator writes management changes to `APIM_CONFIG_PATH`; the base Compose stack puts that file under `/tmp` on `tmpfs`, so container stop/restart or replacement discards those edits. OIDC and OTEL stacks have named provider/telemetry volumes. `make down` does not remove volumes. | [Document seeded, temporary and volume state separately](LOCAL-LIFECYCLE.md), with backup and a deliberate reset for the chosen stack. |
| Offline use | Service Bus lists offline work as a benefit. Neither page promises that every first-time setup or image acquisition can happen without a network. | Docker builds use upstream base images and package dependencies; some defaults use Docker Hardened Images and README setup may require `docker login dhi.io`. `uv` and npm roots also install dependencies. | Describe offline use as conditional on source, package, and image caches already being present. Avoid a blanket offline guarantee. A documented warm-cache workflow would make the limit practical and testable. |
| Setup and cost | Both avoid cloud usage charges; Service Bus calls out containerized, cross-platform use. | This repo is also Docker-first and local, but has multiple optional stacks, host port checks, `mkcert` for TLS, and a larger dependency footprint than a single-service emulator. | State that local operation avoids APIM/Azure resource charges while still requiring a working Docker host, disk, CPU/RAM, and potentially image-registry access. Keep a smallest-stack path easy to find. |
| Versioning and lifecycle | Azurite says it is actively updated for Storage API versions and points to GitHub milestones. Service Bus documents quotas and startup-configuration behavior for its current version. | The package is versioned (`0.4.0` in `pyproject.toml`), with a release workflow and dependency lock. The common Compose path builds local images tagged `latest`; several third-party images are tagged, and the LGTM image is digest-pinned. | Track simulator release/version, supported Python/APIM behavior references, and third-party image versions separately. For reproducible reports, include the simulator version and fixture/reference date. Consider pinning externally pulled images where repeatability matters. |
| Support and disclaimers | Service Bus explicitly says it has no official support and no SLA; Azurite routes issues and feature requests to its open-source GitHub project. | README identifies the project as unofficial and independent of Microsoft, points to the project's issues, and warns against internet exposure and production use. The repository has its own FSL-1.1-MIT license. | Keep affiliation and support visible on the README and browser surfaces. A Microsoft emulator's support status does not confer support on this project. |

## Lessons already present

- [Scope](SCOPE.md) states the goal and exclusions plainly and gives readers a
  route to feature-level detail.
- [Fidelity contracts](FIDELITY-CONTRACTS.md) make bounded claims auditable:
  documented input/output, exclusions, a primary reference, local regression,
  and a confidence label.
- [The Azure comparison lab](../examples/azure-validation/README.md) compares
  selected fixtures with a live APIM instance and records cases blocked by
  Azure Policy. This helps keep documentation claims distinct from live
  differential evidence.
- The README already says the project is for local iteration, not production,
  warns against internet exposure, lists Docker prerequisites, and identifies
  small gateway-only startup alongside richer stacks.
- The `uv.lock` file, seven-day dependency cooldown, release version check, and
  digest-pinned LGTM image show that reproducibility is already a project
  concern, though external image pinning is not uniform.

## Applied in this quality pass

The README and browser surfaces identify the independent project. The README
puts the lean gateway path and three URL roles first. The
[lifecycle guide](LOCAL-LIFECYCLE.md) explains config backup, scoped reset,
image reuse, local resource costs and conditional network needs. The
[API lesson](API-BASICS.md) uses concrete calls, access checks and traces rather
than requiring another service or a cloud account.

## Further improvements

1. **Improve repeatability of compatibility evidence.** Include simulator
   release, fixture revision, and reference review date in Azure comparison
   reports. Pin external images where a moving tag could change test results,
   especially for report-producing workflows.
2. **Measure resource and warm-start costs.** Record Docker host details and
   the exact stack when measuring startup time, memory and disk. Avoid universal
   minimums or speed claims until measured. Keep OIDC, LocalStack and LGTM
   optional for tests that need them.
3. **Verify offline operation explicitly.** A future cold/warm-cache exercise
   should enumerate images and package caches, then verify the chosen fixture
   with outbound access disabled. Local URLs alone do not prove isolation.

These are recommendations for this project; Microsoft emulator behavior is not
evidence that APIM behaves the same way as either emulator or simulator.
