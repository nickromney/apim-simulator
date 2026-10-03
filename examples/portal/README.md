# Portal comparison fixture

## Scalar browser lab

Run the versioned JSON echo fixture with:

```sh
make -C examples/portal up
make -C examples/portal smoke
make -C examples/portal down
```

Open <http://localhost:8000/apim/portal>. The smoke uses installed Google Chrome
and development-only Playwright tooling. To use a downloaded Chromium instead,
run `npm --prefix examples/portal exec -- playwright install chromium` and set
`PLAYWRIGHT_CHANNEL=chromium` when running smoke. `STACK_SLOT=1` shifts the
published port to 8100; `APIM_BASE_URL` can target another instance of this fixture.

The browser blocks every external request, renders the OpenAPI contract, selects
v2 and the custom subscription header, sends the example JSON, edits the body,
checks the actual response, verifies that the key is not persisted, then changes
identity and confirms that the previous key disappears. The fixture uses local
`return-response` policies and intentionally synthetic demo credentials.

## Creation and revision comparison

The OpenAPI document describes a small contract adapted from the platform subnet
calculator: Health and IPv4 subnet information. Its backend URL intentionally
uses `example.invalid`; importing it does not deploy or call a real backend.

The runtime fixture preserves the browser-verified HTTP creation/revision journey:
`subnetcalc-simulator-comparison`, Health operation, independent revisions 1 and
2, and a revision-2 release note. Local `return-response` policies identify the
revision without contacting a backend. Revision 2 is current; revision 1 remains
available through `;rev=1`.

Start it with:

```sh
docker compose -f compose.yml -f compose.public.yml -f compose.ui.yml -f compose.portal-journey.yml up --build -d
```

Open <http://localhost:3007>, load the local demo preset and connect. These are local
development fixtures and use the repository's intentional demo credentials.
The normal stack's temporary runtime config resets when the container restarts;
this overlay reloads the recorded comparison fixture at startup.

The fixture also includes `subnetcalc-openapi`, imported from the same
`subnetcalc.openapi.json` file into the local simulator and the existing Azure
APIM service. It has `GET /health` and `POST /ipv4/subnet-info`, a JSON request
example, a referenced request schema, and 200 response descriptions. Both use
the placeholder backend; this proves management/import comparison, not real
backend connectivity. The original revision demo remains a separate API.
