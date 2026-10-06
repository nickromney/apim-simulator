# Repository Guidelines

- For system design or agent-operated workflows, read [the operating model](docs/AGENT-SYSTEM.md); it maps authorities, state, control, and verification.

Use this file for durable, concise guidance for coding agents in this repository.

- Treat tutorial outcomes as local simulation requirements; use local UI/API clients such as Bruno without emulating Azure CLI or writing editor extensions.
- Before changing code, read `README.md` and the nearest package/build manifest for the commands and constraints that apply.
- For expression-cache, route-matching, or gateway-benchmark changes,
  read [the agent notes](docs/GATEWAY-OPTIMIZATION-LEARNINGS.md).
- Run Python tests and lint with `uv run --extra dev`; do not invoke `.venv/bin/python` directly.
- Keep new example lifecycle commands in `examples/<name>/Makefile`; the root lists entrypoints and delegates repo-wide validation.
- When forwarding a transformed body or assembling a callout/resolver request, let HTTPX recalculate Content-Length; do not copy the incoming length.
- Trace serialization must preserve request and callout bodies; enabling tracing must not change policy results.
- When patching `app/policy.py`, include its containing policy/helper context; repeated `PolicyRequest` constructors have different variable-sharing semantics.
- Keep example XML in Microsoft's documented element order; the local parser can accept orders Azure rejects.
- Verify Docker smoke through published localhost ports; in-container checks can miss inaccessible host ports.
- Public examples, reports, and docs must use placeholders or environment inputs for real deployment identifiers, including storage accounts, subscriptions, resource names, endpoints, and private IPs.
- Add confirmed project-specific commands, conventions, and constraints here when they become durable.

## Codex workflow

- Keep this file short, concrete, and repo-specific. Capture layout, commands, conventions, constraints, and done criteria; move repeatable procedures to scoped skills/docs.
- For each task, state the goal, relevant context/files, constraints, and verification criteria. Plan complex or ambiguous work before editing.
- Keep one thread per coherent outcome. Read only relevant files; delegate bounded exploration/tests when useful, and use worktrees for parallel work.
- Verify changes with focused tests and applicable lint, formatting, type checks, builds, and diff review; report checks run or skipped.
- Prefer least-privilege permissions and dry-runs. Add MCP/tools only when they remove a real repeated loop.
- Use background or scheduled work for long-running or recurring tasks instead of continuous polling.
- After a repeated mistake or correction, update this file with the smallest actionable rule that would prevent it.

Reference: [Codex best practices](https://learn.chatgpt.com/guides/best-practices)
