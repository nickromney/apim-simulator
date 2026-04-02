# Bruno Collection

This collection exercises the APIM-backed todo flow end to end:

- health through APIM
- CORS preflight
- missing and invalid subscription key cases
- list, create, toggle, and final list verification

Load this folder as a Bruno collection and select `environments/local.bru`, or
run it from the repo root with:

```bash
make test-todo-bruno
```

To point the same collection at a different ingress later, edit the environment
variables rather than the request files.
