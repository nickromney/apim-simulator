# Agent system implementation plan

## Goal and bounds

Make the simulator legible as a linked system of intent, state, execution,
observation, and evidence. Improve the existing local control interface with
minimal dependencies and preserve gateway and management response contracts.
This pass covers navigation, action inspection, effective policy access, and
verified operating guidance. It introduces no autonomous scheduler, cloud
resources, or alternative management engine.

## Plan and acceptance

1. Map the authority and abstraction boundaries. Deliver `AGENT-SYSTEM.md`
   with source ownership, context-sensitive reading paths, state distinctions,
   and explicit completion/evidence rules.
2. Link active design and roadmap entrypoints to the model. Keep historical
   reports intact and keep `AGENTS.md` concise. The roadmap must apply the
   observe/inspect/apply/verify loop to future features.
3. Add offline CLI command discovery and request previews, generated from the
   existing command registry and execution planner. Verify no network request,
   no tenant credential output, confirmation on execution, local input errors,
   and correspondence between preview and actual request.
4. Expose existing effective-policy inspection with product context through the
   CLI. Verify against the application and verify invalid option combinations.
5. Run focused application tests, Python formatting/lint, Markdown validation,
   and diff review. Record actual outcomes below; distinguish unavailable checks.

## Decisions

Use the existing CLI and management APIs because they already connect operator
intent to application behavior. Keep preview offline to make failed or absent
runtime setup inexpensive. Report replay as `execute` because it can affect
backends and runtime state. Preserve authored input in previews so they are
reviewable; keep credentials out and document sensitive body handling.
Prefer conditional document pointers over another exhaustive component catalog.

## Progress — complete (2026-10-06)

- Architecture and operating model written; active entrypoint links added.
- Command discovery, request preview, and effective-policy options implemented.
- Focused CLI checks: 21 tests passed, including no-network preview/discovery,
  preview/execution correspondence, deletion confirmation, URL credential
  omission, input failure, and effective product-context inspection.
- Python formatting and Ruff lint passed for both changed Python files.
- Markdown lint passed with the repository configuration, including new files;
  new-document links resolve and `git diff --check` passed.
- Manual command-discovery invocation returned versioned JSON successfully.
- Application regression command: `uv run --offline --extra dev pytest -m
  'not repo and not integration' -q`, with `UV_CACHE_DIR` under `/tmp` for the
  sandbox. Result: 1,153 passed; 13 localhost socket-binding tests were denied
  by the sandbox. Rerunning those 13 with local socket access passed all 13.
  The final additional URL-credential regression passed in the separate
  21-test CLI run. Repository-artifact and external integration tests were
  excluded from this application run.
- Docker smoke, frontend checks, and live Azure comparison are outside this
  CLI/documentation change; no gateway, browser, or fidelity semantics changed.
- Diff review found no remaining actionable defects.

## Sibling follow-up — 2026-10-06

Verified the local Foundry checkout matches GitHub HEAD `e7be021b34`.
Reviewed its latest agent-model/inspection implementation and prior pairing
lab changes. Added APIM `inspect` using five existing read-only endpoints with
versioned JSON, non-atomicity, partial HTTP/transport failure preservation,
and offline preview. Added application, authorization, non-JSON/transport,
and no-network preview regressions. Recorded independent service/credential
ownership and direct/forwarded diagnosis in the operating model. The Foundry
checkout was read only; no cloud resources or pairing stacks were started.

Follow-up checks: 25 CLI tests passed, including the four inspection regressions;
Python formatting/lint, Markdown validation and diff checks passed.
