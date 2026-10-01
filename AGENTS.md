# Repository Guidelines

Use this file for durable, concise guidance for coding agents in this repository.

- Before changing code, read `README.md` and the nearest package/build manifest for the commands and constraints that apply.
- Run Python tests and lint with `uv run --extra dev`; do not invoke `.venv/bin/python` directly.
- Keep new example lifecycle commands in `examples/<name>/Makefile`; the root lists entrypoints and delegates repo-wide validation.
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
