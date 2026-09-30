# Operator Console

The browser UI uses a compact Azure APIM-inspired layout with separate API and operation navigation and **Design**, **Settings**, and **Test** tabs. It provides a focused workflow for managing APIs in the local APIM simulator. It is intended for simulator operations and authoring; it does not reproduce the full Azure portal.

## Connect

Start the simulator with its management plane enabled, then open the UI. Enter the gateway base URL and tenant key and choose **Connect**. **Load Local Demo** fills the default local development URL and tenant key; choose **Connect** afterward to load current state. The console stores these two connection values in browser local storage.

The API Explorer searches API IDs, names, paths, and operation IDs, display names, methods, and templates. Selecting an API or operation opens its workflow and loads that resource’s policy scope. If an edit is dirty, changing the selected resource prompts before discarding it. Leaving the page also triggers the browser's unsaved-changes warning.

## API and operation authoring

Choose **New API**, then **HTTP** or **OpenAPI**. HTTP opens the API settings form; OpenAPI creates and imports in one flow from a local JSON/YAML file or a supported document URL. An import failure leaves the created API available for correction and retry.

In **Settings**, create an API or edit its display name, path, upstream URL and prefix, and required-query translation behavior. Save API applies only these edits while preserving the API's other configured fields and policy. Existing API settings can be edited without replacing its product links, protocols, version settings, tags, or policies.

Select an operation to edit its display name, HTTP method, URL template, and description. Creating or saving an operation preserves its imported request/response metadata and operation policy. Deleting APIs or operations requires an explicit browser confirmation.

OpenAPI import is available for a saved API. Paste a document or provide a URL using a supported link format. Supported document scope is Swagger 2 JSON and OpenAPI 3.0.x (up to 3.0.3); OpenAPI 3.1 is rejected. The importer reports unsupported constructs instead of claiming support for the complete Azure APIM import surface. **Required query parameters** controls whether required query parameters are represented in operation templates or retained as query parameters.

## Policies

The **Design** workflow edits global, product, API, operation, and legacy route policy XML. Choose the scope, edit the document, and save. **Show effective policy** loads the composed policy as read-only XML. Product context is optional and can be changed to inspect product-specific inheritance. Switch back to **Edit scope policy** to author the selected resource's policy. Fragment includes are expanded in the effective view; other named-value placeholders remain authored.

## Test requests and traces

The **Test** workflow builds a request from the selected operation's imported metadata. Path, query, and header controls use available defaults, examples, and enumerated values. The request body starts with an imported example or a sample generated from its schema. The console adds configured API version and subscription key values to the appropriate header, query parameter, or path segment. Choose a saved subscription or paste a key. **Send request** calls the simulator's management replay endpoint and shows response status, body, headers, and its trace link.

**Manual replay** remains available for arbitrary method, path, headers, and body. The Trace Ledger shows route, status, duration, upstream, correlation details, and ordered policy steps, with raw JSON available for inspection. Subscription cards retain approval/rejection and primary/secondary key rotation actions.

The console is a simulator operator surface: it does not claim complete Azure portal parity, full OpenAPI import parity, or support for every APIM resource type.

## Revisions and change log

**Revisions** creates independent copies of the current API definition. A revision has its own settings, operations and policy snapshot. Revision URLs use `;rev=N` after the API suffix. Offline revisions reject gateway requests. **Make current** selects the default definition for requests without a revision selector. **Changelog** records release notes and promotes the selected revision; notes are also available in the consumer portal.
