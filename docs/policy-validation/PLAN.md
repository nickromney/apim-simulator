# Policy guide validation plan — 2026-10-02

Goal: reproduce the outcomes of the thirteen policy guides listed by the user
through local UI/API workflows. Tutorial implementation is committed as be578e3.
Use primary Microsoft documentation and its concrete examples to identify gaps;
parser acceptance is insufficient evidence of correct execution.

Validate policy overview, authoring/editing, local equivalents for AI/editor
assistance, expressions, fragments, errors, advanced logging/throttling,
external callouts, Service Bus pub/sub, named values and GraphQL resolvers.

Add a separate Compose pub/sub container with publishers, topics, independent
subscriptions and consumers. Use it for messaging and buffered logging; route
notification examples to a local capture endpoint. Keep deployment identifiers
as placeholders/environment inputs. Preserve request/callout bodies and results
when tracing or logging is enabled. Keep source XML element ordering.

Verification: focused documentation-backed gateway tests, all thirteen live
journeys through published localhost ports, Python suite, applicable lint,
formatting, shell and Compose checks, and adversarial diff review. Record each
source, adaptation, assertion and actual result in the final report.

Completed: all thirteen published-port journeys passed twice. Final evidence is
in `validation-2026-10-02.md` and `live-results-2026-10-02.json`.
