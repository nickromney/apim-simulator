# Cyclomatic Complexity

McCabe's measure counts the linearly independent paths through a function: one
for the straight line, plus one for every point the flow can branch. The number
is a review prompt, not a verdict. A function scoring 14 is telling you it holds
fourteen paths a reader has to keep straight, and that a name is probably hiding
in there.

## Running it

The gate is ruff's `C901`, wired into `make lint` through `[tool.ruff.lint.mccabe]`
in `pyproject.toml`. To see what is over the line:

```bash
make complexity
```

To find the next function to split, ask for everything above a threshold,
highest first:

```bash
make complexity-report THRESHOLD=8
```

## The ratchet

`max-complexity` is **8**, which was the target. It got there in two moves: set
at 14 to hold the line that already existed, then lowered once the 24 functions
above 8 had been split.

The number only ever goes down. Never raise it to admit a new function. A gate
that fails on arrival gets suppressed rather than fixed, which is why it was
introduced as a ratchet rather than as the target value with a backlog of
`noqa`s behind it.

## Where it stood, and where it stands

Every function in `app/`, `scripts/`, `tests/` and `examples/` is now at or below
8. Thirty-three were above it when this started.

| Function | Before | After |
| --- | ---: | ---: |
| `build_management_router` | 147 | composed from 10 routers |
| `import_from_tofu_show_json` | 99 | 2 dispatch tables |
| `execute_gateway_request` | 44 | 8 named stages |
| `create_app` | 42 | composition root |
| `_parse_node` | 31 | dispatch table |
| `_dispatch` (CLI) | 27 | command table |
| `parse_condition` | 10 | recogniser table |

`app/policy.py` held thirteen of the thirty-three and now holds none.

## What the big ones were actually made of

Three of the worst four were not branchy at all, which is the main thing this
pass taught.

- **`build_management_router` scored 147 for declaring 95 routes.** Ruff counts a
  nested function definition as a branch of its enclosing function, so a router
  builder's score is its route count. Splitting it into ten resource-scoped
  builders made it readable, and the builders still score their own route counts.
- **`import_from_tofu_show_json` scored 99 for a long `if res.type == ...` chain.**
  Two dispatch tables and a shared accumulator turned each resource type into a
  small named function.
- **`create_app` scored 42 for defining closures**, not for deciding anything. Its
  collaborators are module-level now, and directly testable as a result: the
  config watcher used to be reachable only by walking `create_app.__code__`.

## Route-registration builders

`_build_*_router` functions carry a scoped `# noqa: C901`. Their score is the
number of routes they register, not decisions a reader has to hold: a reader
scanning `_build_apis_router` follows one path, not thirty-three. Splitting them
further would mean arbitrary eight-route chunks that make the surface harder to
find, not easier.

This is the one place the measure genuinely does not mean what it usually means.
Anywhere else, a high score is a real signal and the answer is to split the
function, not to annotate it.
