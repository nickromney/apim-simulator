"""Source-backed dashboard composition and failed introspection journeys.

Microsoft source XML specimens 07–09 are in tests/fixtures/policy_guides.
Adaptations: close set-variable tags and quote their XML attributes, escape
URL ampersands/C# generic brackets, replace service hosts with the local metric
backend, and remove the illustrative existing-response-variable placeholder.
The published source passes fromDate to both backend parameters. Check that
exact behavior first, then refine the authored policy to forward toDate.
"""

from __future__ import annotations

import os
from pathlib import Path
from xml.sax.saxutils import quoteattr

import httpx

FIXTURES = Path(__file__).resolve().parents[2] / "tests/fixtures/policy_guides/api-management-sample-send-request"
TENANT = {"X-Apim-Tenant-Key": os.environ.get("APIM_TENANT_KEY", "local-dev-tenant-key")}
METRICS = {
    "revenuedata": "salesdata",
    "materialdata": "materiallevels",
    "throughputdata": "throughput",
    "accidentdata": "accidentdata",
}


def _manage(client, method, path, body=None):
    response = client.request(method, "/apim/management/" + path, headers=TENANT, json=body)
    response.raise_for_status()
    return response.json()


def composition_policy(*, use_to_date=False):
    backend = os.environ.get("POLICY_BACKEND_URL", "http://mock-backend:8080/api").rstrip("/")
    variables = "".join(
        f'<set-variable name="{name}" value=' + quoteattr('@(context.Request.Url.Query["' + name + '"].Last())') + " />"
        for name in ["fromDate", "toDate"]
    )
    callouts = (FIXTURES / "example-08.xml").read_text()
    for host in ["https://accounting.acme.com", "https://inventory.acme.com", "https://production.acme.com"]:
        callouts = callouts.replace(host, backend + "/metrics")
    callouts = callouts.replace("&to=", "&amp;to=")
    if use_to_date:
        callouts = callouts.replace(
            'to={(string)context.Variables["fromDate"]}', 'to={(string)context.Variables["toDate"]}'
        )
    response = (FIXTURES / "example-09.xml").read_text()
    response = response.replace(' response-variable-name="existing response variable"', "").replace(
        "<JObject>", "&lt;JObject&gt;"
    )
    return (
        "<policies><inbound>"
        + variables
        + callouts
        + response
        + "</inbound><backend /><outbound /><on-error /></policies>"
    )


def _create(client, identifier, xml):
    _manage(
        client,
        "PUT",
        "apis/" + identifier,
        {"name": identifier, "path": identifier, "upstream_base_url": "http://mock-backend:8080/api"},
    )
    _manage(
        client,
        "PUT",
        f"apis/{identifier}/operations/dashboard",
        {"name": "Dashboard", "method": "GET", "url_template": "/dashboard"},
    )
    _manage(client, "PUT", "policies/api/" + identifier, {"xml": xml})


def run(client: httpx.Client) -> dict:
    identifier = "policy-dashboard"
    try:
        _create(client, identifier, composition_policy())
        params = {"fromDate": ["ignored", "2026-01-01"], "toDate": "2026-01-31"}
        original = client.get(f"/{identifier}/dashboard", params=params)
        assert original.status_code == 200, original.text
        assert set(original.json()) == set(METRICS)
        assert all(value["from"] == value["to"] == "2026-01-01" for value in original.json().values())
        _manage(client, "PUT", "policies/api/" + identifier, {"xml": composition_policy(use_to_date=True)})
        token = _manage(client, "POST", "gateways/managed/listDebugCredentials", {"apiId": identifier})["token"]
        plain = client.get(f"/{identifier}/dashboard", params=params)
        traced = client.get(f"/{identifier}/dashboard", params=params, headers={"Apim-Debug-Authorization": token})
        assert plain.status_code == traced.status_code == 200 and plain.json() == traced.json()
        for name, metric in METRICS.items():
            assert plain.json()[name]["metric"] == metric
            assert plain.json()[name]["from"] == "2026-01-01" and plain.json()[name]["to"] == "2026-01-31"
        trace = _manage(client, "POST", "gateways/managed/listTrace", {"traceId": traced.headers["Apim-Trace-Id"]})
        writes = [entry for entry in trace["policy_variable_writes"] if entry["name"] in METRICS]
        assert [entry["name"] for entry in writes] == list(METRICS)
        assert all(entry["value"]["body_text"] for entry in writes)
        assert not any(step["step"] == "forward-request" for step in trace["policy_steps"])
        # ignore-error produces null on transport failure; guard it before body access.
        null_xml = '<policies><inbound><send-request mode="new" response-variable-name="tokenstate" ignore-error="true" timeout="1"><set-url>http://mock-backend:1/introspection</set-url><set-method>POST</set-method></send-request><choose><when condition="@(context.Variables[&quot;tokenstate&quot;] == null)"><return-response><set-status code="401" reason="Unauthorized" /><set-header name="WWW-Authenticate"><value>Bearer error="invalid_token"</value></set-header></return-response></when></choose></inbound><backend /><outbound /><on-error /></policies>'
        _manage(client, "PUT", "policies/api/" + identifier, {"xml": null_xml})
        failed = client.get(f"/{identifier}/dashboard")
        assert failed.status_code == 401 and failed.headers["WWW-Authenticate"] == 'Bearer error="invalid_token"'
        return {
            "four_request_composition": True,
            "last_query_value": True,
            "source_from_date_typo_observed_and_refined": True,
            "trace_preserves_callout_bodies": True,
            "null_introspection_denied": 401,
        }
    finally:
        _manage(client, "DELETE", "apis/" + identifier)
