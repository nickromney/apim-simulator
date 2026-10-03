# Local APIM policy guide lab

Reproduce the thirteen [Microsoft policy guides](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-policies) through the simulator's management API and gateway. This lab uses its own Compose project and published localhost ports, so it can run beside the usual simulator stack.

```sh
make -C examples/apim-policies smoke
make -C examples/apim-policies verify # repeat against the running lab
make -C examples/apim-policies down
```

The gateway is `http://localhost:8900`; the companion service is `http://localhost:8901`. Override `APIM_GATEWAY_PORT` and `POLICY_BROKER_PORT` if necessary. `smoke` builds the gateway, mock backend and companion container, then runs assertions from the host using `uv run --extra dev`. Docker and uv are required.

The separate SQLite-backed companion container provides a small HTTP pub/sub service: queues deliver FIFO, topics copy messages to each existing subscription, consumers acknowledge by consuming, and expired messages are discarded. Sender credentials can publish; administrator credentials create entities and consume messages. It also provides reference-token introspection and a secret store with separate read-only credentials. Container data lives in a temporary filesystem and resets when the container is replaced. This is a local equivalent for the guide workflows; it does not implement AMQP, settlement modes or Azure's distributed delivery infrastructure.

| Guide | Local assertion |
|---|---|
| Policies overview | Scope inheritance, body replacement and global cross-domain endpoint |
| Set/edit | Save, read, effective policy order and atomic rejection of invalid XML |
| Copilot authoring | Execute the documented prompt outcomes: five calls per second, remove a response header and select fields by validated user role |
| VS Code debugging | Management authoring, variables/steps, debug trace and body-preserving replay |
| Expressions | User/deployment context, date/string expressions and conditional policy execution |
| Fragments | Create, include, update and validate reusable policy fragments |
| Error handling | LastError context and on-error response status, headers and body |
| Advanced logging | Request/response capture, correlation, partitions, credential filtering and unchanged bodies |
| Advanced throttling | Classic and token-bucket counters, IP/JWT/custom keys and bandwidth quotas |
| External services | Reference-token introspection and background failure notifications |
| Service Bus | Topic fanout, independent subscribers, queues, TTL/properties and immediate response |
| Named values | Secret masking, display-name rename updates and live secret rotation |
| GraphQL | Schema, HTTP field resolvers, arguments and nested parent values |

`authoring.py`, `throttling.py`, and `expressions_graphql.py` contain the guide journeys; `verify.py` coordinates them with messaging, logging and callouts. The runs create lab resources and drain only their own broker entities before assertions. Use a dedicated local lab configuration when overriding `APIM_BASE` or `BROKER_BASE`.

The source inventory and XML specimens are in [docs/policy-validation](../../docs/policy-validation/INVENTORY.md) and [tests/fixtures/policy_guides](../../tests/fixtures/policy_guides/README.md). Source hashes and adaptations distinguish preserved examples from local equivalents. AI/editor guides validate the resulting policies with local authoring and debug APIs. Advanced logging uses the body-preserving `ToHttpMessage` helper in place of the guide's C# LINQ serialization block.

Local adapter configuration lives in `apim.json`: `local_messaging.namespaces` maps namespace names to explicit HTTP broker endpoints and allowed sender identities. Logger `eventhub.endpoint_uri` selects that local binding. `APIM_LOCAL_VAULT_BASE_URL` and `APIM_LOCAL_VAULT_KEY` opt into the local secret endpoint for named values with vault references; rotation is resolved on each request. Existing `APIM_NAMED_VALUE_<ID>` overrides still take precedence. Keys are local demo defaults and can be supplied through `POLICY_BROKER_ADMIN_KEY`, `POLICY_BROKER_SENDER_KEY` and `POLICY_VAULT_READER_KEY`.
