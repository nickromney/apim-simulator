# Set request method — verified subset

`<set-method>POST</set-method>` changes the effective request method in the
inbound or on-error policy section. The value supports policy expressions.
Subsequent `context.Request.Method` expressions observe the new method, and
forwarding sends that method with the existing or transformed request body.
HTTPX calculates the outgoing Content-Length. The original incoming method
still selects the API operation; changing the method does not rematch routes.

Method values are normalized to uppercase and must be HTTP token strings.
Empty values, child elements, unknown attributes and backend/outbound use
are rejected. Extension methods such as `PROPFIND` are accepted. The trace
step records both original and effective methods. A transformed POST does
not use the gateway GET cache.

The `set-method` child inside `send-request` and `send-one-way-request` remains
local to that callout; it does not change the enclosing request method.

Reference: [Microsoft set-method policy](https://learn.microsoft.com/en-us/azure/api-management/set-method-policy).
Evidence: `tests/test_set_method_policy.py` (`POLICY-SET-METHOD`).
