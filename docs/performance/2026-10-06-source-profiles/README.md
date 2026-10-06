# Source-only CPU/allocation attribution — 6 October 2026

These separate cProfile/tracemalloc runs use the identical synthetic request inputs. Their profiled timing outputs are retained as raw evidence but are excluded from all timing comparisons. Background load was not controlled; cumulative parents overlap, coroutine resumes affect counts, and cProfile elapsed times may include scheduling effects. No optimizer change is proposed.

| Condition | Rank | Source | Self seconds | Evidence |
| --- | ---: | --- | ---: | --- |
| expressions-cached | 1 | `app/apim_expr.py:27:__getitem__` | 0.070091 | [expressions-cached-source-cpu.json](expressions-cached-source-cpu.json) |
| expressions-cached | 2 | `app/apim_expr.py:433:build_expression_context` | 0.059115 | [expressions-cached-source-cpu.json](expressions-cached-source-cpu.json) |
| expressions-cached | 3 | `app/apim_expr.py:931:_evaluate_expression` | 0.051951 | [expressions-cached-source-cpu.json](expressions-cached-source-cpu.json) |
| expressions-cached | 4 | `app/apim_expr.py:22:__init__` | 0.047338 | [expressions-cached-source-cpu.json](expressions-cached-source-cpu.json) |
| expressions-cached | 5 | `app/apim_expr.py:397:_normalize_request` | 0.035469 | [expressions-cached-source-cpu.json](expressions-cached-source-cpu.json) |
| expressions-uncached | 1 | `app/apim_expr.py:931:_evaluate_expression` | 0.124444 | [expressions-uncached-source-cpu.json](expressions-uncached-source-cpu.json) |
| expressions-uncached | 2 | `app/apim_expr.py:587:_translate_code_fragment` | 0.107034 | [expressions-uncached-source-cpu.json](expressions-uncached-source-cpu.json) |
| expressions-uncached | 3 | `app/apim_expr.py:548:_rewrite_outside_strings` | 0.088430 | [expressions-uncached-source-cpu.json](expressions-uncached-source-cpu.json) |
| expressions-uncached | 4 | `app/apim_expr.py:653:_find_top_level_question` | 0.066412 | [expressions-uncached-source-cpu.json](expressions-uncached-source-cpu.json) |
| expressions-uncached | 5 | `app/apim_expr.py:433:build_expression_context` | 0.065750 | [expressions-uncached-source-cpu.json](expressions-uncached-source-cpu.json) |
| versioned-routes-cached | 1 | `app/proxy.py:149:_request_scheme` | 0.110415 | [versioned-routes-cached-source-cpu.json](versioned-routes-cached-source-cpu.json) |
| versioned-routes-cached | 2 | `app/proxy.py:210:_match_versioned_candidate` | 0.070025 | [versioned-routes-cached-source-cpu.json](versioned-routes-cached-source-cpu.json) |
| versioned-routes-cached | 3 | `app/proxy.py:303:_resolve_route_candidate` | 0.065741 | [versioned-routes-cached-source-cpu.json](versioned-routes-cached-source-cpu.json) |
| versioned-routes-cached | 4 | `app/proxy.py:155:_route_protocol_allowed` | 0.063046 | [versioned-routes-cached-source-cpu.json](versioned-routes-cached-source-cpu.json) |
| versioned-routes-cached | 5 | `app/config.py:682:_match_segments` | 0.049898 | [versioned-routes-cached-source-cpu.json](versioned-routes-cached-source-cpu.json) |
| versioned-routes-uncached | 1 | `app/config.py:773:_match_path_prefix` | 0.288033 | [versioned-routes-uncached-source-cpu.json](versioned-routes-uncached-source-cpu.json) |
| versioned-routes-uncached | 2 | `app/config.py:646:_path_segments` | 0.216372 | [versioned-routes-uncached-source-cpu.json](versioned-routes-uncached-source-cpu.json) |
| versioned-routes-uncached | 3 | `app/proxy.py:149:_request_scheme` | 0.191173 | [versioned-routes-uncached-source-cpu.json](versioned-routes-uncached-source-cpu.json) |
| versioned-routes-uncached | 4 | `app/config.py:639:_normalize_path` | 0.121721 | [versioned-routes-uncached-source-cpu.json](versioned-routes-uncached-source-cpu.json) |
| versioned-routes-uncached | 5 | `app/proxy.py:210:_match_versioned_candidate` | 0.105977 | [versioned-routes-uncached-source-cpu.json](versioned-routes-uncached-source-cpu.json) |

Allocation evidence starts after imports and includes app construction, all request warmups, measured requests, and app/context cleanup. `traced_peak_bytes` is traced Python high-water memory, not RSS or retained cache bytes. `app_retained_at_workload_end` ranks application line survivors; it does not measure allocation traffic or distinguish cache retention from monitoring/configuration retention.

| Condition | Current traced bytes | Peak traced bytes | Evidence |
| --- | ---: | ---: | --- |
| expressions-cached | 7300320 | 7782755 | [expressions-cached-allocations.json](expressions-cached-allocations.json) |
| expressions-uncached | 7504332 | 7635730 | [expressions-uncached-allocations.json](expressions-uncached-allocations.json) |
| versioned-routes-cached | 8353987 | 8584801 | [versioned-routes-cached-allocations.json](versioned-routes-cached-allocations.json) |
| versioned-routes-uncached | 7767498 | 8102609 | [versioned-routes-uncached-allocations.json](versioned-routes-uncached-allocations.json) |

Hypotheses: repeated expression translation and route helper work are supported as source inspection leads by the uncached function/call evidence; actual I/O, off-CPU waits, contention, remote runtime costs and causal speedup remain unmeasured. These profiles justify further investigation rather than optimization. Binary `.pstats` and JSON source function rows are retained, with SHA-256 receipts.
