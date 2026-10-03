"""Consumer-facing developer-portal workflows.

This is the adapted local equivalent of the Azure developer portal's consumer
surface: browse published products, inspect API operations, request a
subscription, and try calls with a key. Signed bearer identities bind users
to their subscriptions; explicit development compatibility accepts a user
header. Draft content, styles, and publication are persisted separately from
the public portal through the operator editor.
"""

from __future__ import annotations

import os
from html import escape
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import HTTPException, Request

from app.config import (
    ApiConfig,
    GatewayConfig,
    OperationConfig,
    ProductConfig,
    ProductState,
    Subscription,
    SubscriptionKeyPair,
    SubscriptionState,
    UserConfig,
)
from app.portal_customization import PortalSite


def require_portal_user(cfg: GatewayConfig, user_id: str | None) -> UserConfig:
    if not user_id:
        raise HTTPException(status_code=401, detail="Portal user header is required")
    user = cfg.users.get(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Unknown portal user")
    if user.state and user.state.lower() != "active":
        raise HTTPException(status_code=403, detail=f"Portal user is not active (state: {user.state})")
    return user


def portal_user_id(request: Request, cfg: GatewayConfig) -> str:
    from app.control_plane import decode_identity

    identity = cfg.portal.identity
    if identity.enabled and (request.headers.get("authorization") or not identity.allow_legacy_user_header):
        claims = decode_identity(request, identity)
        subject = claims["sub"]
        require_portal_user(cfg, subject)
        request.state.portal_actor = {"subject": subject, "authentication": "signed-jwt"}
        return subject
    user = require_portal_user(cfg, request.headers.get(cfg.portal.user_header))
    request.state.portal_actor = {"subject": user.id, "authentication": "legacy-user-header"}
    return user.id


def portal_identity_users(request: Request, cfg: GatewayConfig) -> dict[str, Any]:
    if not cfg.portal.identity.enabled:
        return {**portal_users(cfg), "authentication": "demo"}
    user_id = portal_user_id(request, cfg)
    user = cfg.users[user_id]
    return {
        "users": [{"id": user_id, "name": user.name or user_id}],
        "authentication": request.state.portal_actor["authentication"],
    }


def user_group_ids(cfg: GatewayConfig, user_id: str) -> set[str]:
    return {group_id for group_id, group in cfg.groups.items() if user_id in group.users}


def product_visible(product: ProductConfig, groups: set[str]) -> bool:
    administrator = "administrators" in {group.casefold() for group in groups}
    if product.state != ProductState.Published or not product.require_subscription:
        return administrator
    # Products with no group links are visible to every portal user. Azure
    # scopes visibility through built-in groups instead; documented as adapted.
    if not product.groups:
        return True
    return bool(set(product.groups) & groups)


def _portal_operation_request(cfg: GatewayConfig, api: ApiConfig, operation: OperationConfig) -> dict[str, Any]:
    version = operation.api_version or api.api_version
    version_set = cfg.api_version_sets.get(operation.api_version_set or api.api_version_set or "")
    prefix = "/" + api.path.strip("/")
    if version and version_set and version_set.versioning_scheme == "Segment":
        prefix = prefix.rstrip("/") + "/" + quote(version, safe="")
    path = prefix.rstrip("/") + "/" + operation.url_template.lstrip("/")
    headers = {}
    if version and version_set and version_set.versioning_scheme == "Header":
        headers[version_set.version_header_name] = version
    if version and version_set and version_set.versioning_scheme == "Query":
        path += ("&" if "?" in path else "?") + urlencode({version_set.version_query_name: version})
    return {"request_url": path, "request_headers": headers}


def _project_portal_api(cfg: GatewayConfig, api_id: str, api: ApiConfig) -> dict[str, Any]:
    version_set = cfg.api_version_sets.get(api.api_version_set or "")
    return {
        "id": api_id,
        "name": api.name,
        "path": api.path,
        "api_version": api.api_version,
        "api_version_set": api.api_version_set,
        "version_set_name": version_set.display_name if version_set else None,
        "versioning": {
            "scheme": version_set.versioning_scheme.value,
            "header_name": version_set.version_header_name,
            "query_name": version_set.version_query_name,
        }
        if version_set
        else None,
        "revision": api.revision,
        "revision_description": api.revision_description,
        "change_log": [
            {"release": release.name, "revision": release.revision, "notes": release.notes}
            for release in api.releases.values()
            if release.notes
        ],
        "operations": [
            {
                "id": operation_id,
                "name": operation.name,
                "method": operation.method,
                "url_template": operation.url_template,
                "description": operation.description,
                **_portal_operation_request(cfg, api, operation),
            }
            for operation_id, operation in api.operations.items()
        ],
    }


def portal_users(cfg: GatewayConfig) -> dict[str, Any]:
    return {
        "users": [
            {"id": user_id, "name": user.name or user_id}
            for user_id, user in cfg.users.items()
            if not user.state or user.state.lower() == "active"
        ]
    }


def portal_catalog(cfg: GatewayConfig, user_id: str) -> dict[str, Any]:
    groups = user_group_ids(cfg, user_id)
    products = []
    for product_id, product in cfg.products.items():
        if not product_visible(product, groups):
            continue
        apis = [_project_portal_api(cfg, api_id, api) for api_id, api in cfg.apis.items() if product_id in api.products]
        products.append(
            {
                "id": product_id,
                "name": product.name,
                "description": product.description,
                "require_subscription": product.require_subscription,
                "approval_required": product.approval_required,
                "terms": product.terms,
                "subscriptions_limit": product.subscriptions_limit,
                "apis": apis,
            }
        )
    return {"user": user_id, "products": products}


def _created_by(user_id: str) -> str:
    return f"portal:{user_id}"


def project_portal_subscription(subscription: Subscription) -> dict[str, Any]:
    return {
        "id": subscription.id,
        "name": subscription.name,
        "state": subscription.state.value,
        "products": list(subscription.products),
        "keys": {"primary": subscription.keys.primary, "secondary": subscription.keys.secondary},
    }


def portal_subscriptions(cfg: GatewayConfig, user_id: str) -> dict[str, Any]:
    items = [
        project_portal_subscription(subscription)
        for subscription in cfg.subscription.subscriptions.values()
        if subscription.created_by == _created_by(user_id)
    ]
    return {"user": user_id, "subscriptions": items}


def create_portal_subscription(
    cfg: GatewayConfig, user_id: str, product_id: str, display_name: str | None = None, accept_terms: bool = False
) -> Subscription:
    groups = user_group_ids(cfg, user_id)
    product = cfg.products.get(product_id)
    if product is None or not product_visible(product, groups):
        raise HTTPException(status_code=404, detail="Product not found")
    if not product.require_subscription:
        raise HTTPException(status_code=400, detail="Product does not use subscriptions")
    if product.terms and not accept_terms:
        raise HTTPException(status_code=400, detail="Accept the product terms before requesting a subscription")
    if product.subscriptions_limit is not None:
        existing = sum(
            1
            for subscription in cfg.subscription.subscriptions.values()
            if subscription.created_by == _created_by(user_id)
            and product_id in subscription.products
            and subscription.state in {SubscriptionState.Active, SubscriptionState.Submitted}
        )
        if existing >= product.subscriptions_limit:
            raise HTTPException(status_code=409, detail="Subscription limit reached for this product")

    sub_id = f"{user_id}-{product_id}"
    existing_ids = {subscription.id for subscription in cfg.subscription.subscriptions.values()}
    sequence = 1
    while sub_id in existing_ids:
        sequence += 1
        sub_id = f"{user_id}-{product_id}-{sequence}"

    state = SubscriptionState.Submitted if product.approval_required else SubscriptionState.Active
    subscription = Subscription(
        id=sub_id,
        name=display_name or f"{user_id} / {product.name}",
        keys=SubscriptionKeyPair(primary=f"sub-{sub_id}-primary", secondary=f"sub-{sub_id}-secondary"),
        state=state,
        products=[product_id],
        created_by=_created_by(user_id),
    )
    cfg.subscription.subscriptions[sub_id] = subscription
    return subscription


PORTAL_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>APIM Simulator Developer Portal</title>
<style>
  :root {
    color-scheme: light;
    --bg: #f2ede1;
    --panel: #ffffff;
    --ink: #242424;
    --muted: #605e5c;
    --line: #d2d0ce;
    --accent: #12705f;
    --accent-soft: rgba(18, 112, 95, 0.14);
    --warn-soft: rgba(190, 93, 38, 0.2);
  }
  * { box-sizing: border-box; }
  body { margin:0; font-family:"Segoe UI", SegoeUI, -apple-system, BlinkMacSystemFont, sans-serif; color:var(--ink); background:var(--bg); line-height:1.5; }
  main { max-width:1200px; margin:auto; padding:2rem 1.5rem 4rem; }
  h1 { font-size:1.8rem; letter-spacing:-.035em; margin:0; }
  .lede { color:var(--muted); margin:.5rem 0 2rem; max-width:70ch; }
  section { background:var(--panel); border:1px solid var(--line); border-radius:2px; padding:1.5rem; margin-bottom:1.25rem; }
  h2 { margin:0 0 1rem; font-size:1.1rem; letter-spacing:-.015em; }
  label { display:flex; flex-direction:column; gap:.4rem; font-size:.85rem; color:var(--muted); min-width:0; }
  input, select, button { font:inherit; min-height:42px; padding:.55rem .75rem; border-radius:2px; border:1px solid var(--line); background:var(--panel); color:var(--ink); }
  input, select { width:100%; min-width:0; }
  button { cursor:pointer; background:var(--accent); color:#fff; border-color:var(--accent); white-space:nowrap; }
  button.secondary { background:transparent; color:var(--accent); }
  button:disabled { opacity:.45; cursor:default; }
  :focus-visible { outline:2px solid var(--accent); outline-offset:3px; }
  .identity-grid { display:grid; grid-template-columns:minmax(0,2fr) minmax(0,1fr); gap:1.5rem; align-items:end; }
  .token-entry { display:grid; grid-template-columns:minmax(0,1fr) auto auto; gap:.75rem; align-items:end; }
  .section-description { color:var(--muted); font-size:.9rem; margin:-.5rem 0 1rem; }
  .product { border-top:1px solid var(--line); padding:1rem 0; }
  .product:first-of-type { border-top:none; }
  .product h3 { margin:0; font-size:1rem; }
  .badges { display:inline-flex; gap:.4rem; margin-left:.6rem; flex-wrap:wrap; }
  .badge { font-size:.72rem; padding:.15rem .55rem; border-radius:4px; background:var(--accent-soft); }
  .badge.warn { background:var(--warn-soft); }
  .apis { margin:.5rem 0 1rem; padding-left:1.1rem; color:var(--muted); font-size:.9rem; }
  .apis code { color:var(--ink); }
  .table-scroll { overflow-x:auto; }
  table { width:100%; border-collapse:collapse; font-size:.9rem; }
  th,td { text-align:left; padding:.75rem; border-bottom:1px solid var(--line); }
  th { font-weight:500; color:var(--muted); }
  td.keys { font-family:monospace; font-size:.8rem; overflow-wrap:anywhere; }
  .tryit-grid { display:grid; gap:1rem; grid-template-columns:repeat(3,minmax(0,1fr)); align-items:end; }
  .reference-toolbar { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:1rem; align-items:center; margin:1.25rem 0; }
  .reference-toolbar .status { margin:0; }
  .quick-test { border-top:1px solid var(--line); margin-top:1.5rem; padding-top:1rem; }
  .quick-test summary { cursor:pointer; color:var(--muted); margin-bottom:1rem; }
  pre { background:#171410; color:#f4efe4; padding:1rem; border-radius:2px; overflow:auto; font-size:.82rem; }
  .status { color:var(--muted); font-size:.9rem; margin:.75rem 0 0; }
  .status:empty { display:none; }
  .empty-reference { border:1px dashed var(--line); padding:2rem; text-align:center; color:var(--muted); }
  @media(max-width:720px) { .identity-grid { grid-template-columns:1fr; } .tryit-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } }
  @media(max-width:480px) { main { padding:1.25rem .75rem; } section { padding:1rem; } .tryit-grid,.reference-toolbar { grid-template-columns:1fr; } }
html[data-theme="dark"] { color-scheme:dark; --bg:#111827; --panel:#1f2937; --ink:#f3f4f6; --muted:#c4cbd5; --line:#4b5563; --accent:#60a5fa; }
html[data-theme="light"] { color-scheme:light; }
html[data-theme="dark"] input,html[data-theme="dark"] select { background:var(--panel);color:var(--ink); }
html[data-theme="dark"] button { color:#111827; }
.masthead { display:flex;align-items:center;gap:1.5rem;flex-wrap:wrap;background:#087e78;color:white;padding:.75rem 1.125rem;min-height:56px; }
.masthead strong { font-size:1.05rem;font-weight:600;line-height:1; }
.masthead nav { display:flex;align-items:center;gap:1rem;margin:0;flex-wrap:wrap; }
.masthead nav a,.masthead label { color:white; font-size:.9rem; }
.masthead label { flex-direction:row;align-items:center;gap:.5rem; }
.masthead select { width:auto;min-height:32px;padding:.3rem .5rem; }
</style>
</head>
<body>
  <header class="masthead"><strong>APIM Simulator - developer portal</strong><nav aria-label="Applications"><a href="/apim/portal" aria-current="page">Developer portal</a><a href="__OPERATOR_URL__">Console</a><label>Appearance <select id="theme-select"><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select></label></nav></header>
  <script src="/apim/portal/assets/theme.js"></script>
<main>
  <h1>Developer Portal</h1>
  <p class="lede">
    Browse published products, request a subscription, and try API calls against the local simulator.
    This is the adapted local stand-in for the Azure developer portal's consumer workflows.
  </p>

  <section>
    <h2>Portal identity</h2>
    <p class="section-description" id="identity-description">Choose a local demo user or supply a signed portal token.</p>
    <div class="identity-grid"><div class="token-entry">
    <label>Portal token
      <input id="portal-token" type="password" autocomplete="off" placeholder="Paste a signed portal token" />
    </label>
    <button id="portal-sign-in" type="button">Use token</button><button id="portal-sign-out" type="button" hidden>Sign out</button>
    </div><label><span id="user-label">Demo user</span>
      <select id="user-select"></select>
    </label>
    </div><p class="status" id="identity-status"></p>
    <p class="status" id="user-status" role="status"></p>
  </section>

  <section>
    <h2>Product catalog</h2>
    <div id="catalog"></div>
  </section>

  <section>
    <h2>My subscriptions</h2>
    <div class="table-scroll"><table>
      <thead><tr><th>Name</th><th>State</th><th>Products</th><th>Primary key</th></tr></thead>
      <tbody id="subs-body"></tbody>
    </table></div>
  </section>

  <section>
    <h2>Explore an API</h2>
    <p class="section-description">Choose an API and version, then inspect its documentation or send a request. API documentation and requests powered by <a href="https://github.com/scalar/scalar" target="_blank" rel="noopener noreferrer">Scalar</a>.</p>
    <div class="tryit-grid">
      <label>API<select id="api-select"></select></label>
      <label>Version<select id="version-select"></select></label>
      <label>Subscription key<select id="key-select"></select></label>
    </div>
    <div class="reference-toolbar">
      <p class="status">The selected key is used by the request client.</p>
      <button id="reference-refresh" type="button" class="secondary">Reload API reference</button>
    </div>
    <div id="api-reference"></div>
    <details class="quick-test" id="quick-test">
      <summary>Quick operation check</summary>
      <div class="tryit-grid">
        <label>Operation<select id="op-select"></select></label>
        <label>Path<input id="try-path" placeholder="/hello/greet" /></label>
        <button id="try-send" type="button">Send</button>
      </div>
      <p class="status" id="try-status" role="status"></p>
      <pre id="try-output">Pick an operation and send a request.</pre>
    </details>
  </section>
</main>

<script>
  const state = { user: "", catalog: null, subscriptions: [], apiGroups: new Map(), selectedApi: null, referenceGeneration: 0, identityGeneration: 0 };

  async function renderReference() {
    const generation = ++state.referenceGeneration;
    const root = document.getElementById('api-reference');
    root.replaceChildren();
    const api = state.selectedApi;
    document.getElementById('reference-refresh').disabled = !api;
    document.getElementById('quick-test').hidden = !api;
    if (!api) {
      root.append(el('p', {class:'empty-reference', text:'API documentation will appear here when a published product is available to your user.'}));
      return;
    }
    try {
      const contract = await fetchJson('/apim/portal/apis/' + encodeURIComponent(api.id) + '/openapi');
      if (generation !== state.referenceGeneration) return;
      const frame = el('iframe', { title: 'Scalar API reference and client', src: '/apim/portal/reference',
        style: 'width:100%;height:800px;border:0', referrerpolicy: 'no-referrer' });
      frame.addEventListener('load', () => {
        if (generation !== state.referenceGeneration) return;
        frame.contentWindow.postMessage({ type: 'apim-reference', document: contract,
          key: document.getElementById('key-select').value, dark: document.documentElement.dataset.theme === 'dark' }, location.origin);
      }, { once: true });
      root.append(frame);
    } catch (error) {
      if (generation === state.referenceGeneration) root.textContent = error.message;
    }
  }

  function headers() {
    const token = document.getElementById("portal-token").value.trim();
    if (token) return { "Authorization": "Bearer " + token, "Content-Type": "application/json" };
    return { "X-Apim-Portal-User": state.user, "Content-Type": "application/json" };
  }

  async function fetchJson(path, init) {
    const response = await fetch(path, { ...init, headers: headers() });
    const text = await response.text();
    if (!response.ok) {
      let detail = text;
      try { detail = JSON.parse(text).detail ?? text; } catch {}
      throw new Error(response.status + ": " + detail);
    }
    return text ? JSON.parse(text) : null;
  }

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs ?? {})) {
      if (value == null || value === false) continue;
      if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value);
    }
    for (const child of children ?? []) node.append(child);
    return node;
  }

  function renderCatalog() {
    const root = document.getElementById("catalog");
    root.replaceChildren();
    const products = state.catalog?.products ?? [];
    if (!products.length) {
      root.append(el("p", { class: "status", text: "No published products are visible to this user." }));
      return;
    }
    for (const product of products) {
      const badges = el("span", { class: "badges" }, [
        el("span", { class: "badge", text: product.require_subscription ? "subscription required" : "open access" }),
      ]);
      if (product.approval_required) {
        badges.append(el("span", { class: "badge warn", text: "approval required" }));
      }
      const apis = el("ul", { class: "apis" },
        product.apis.map((api) => {
          const revision = api.revision ? " (rev " + api.revision + ")" : "";
          const item = el("li", {}, [
            el("code", { text: "/" + api.path }),
            " — " + api.name + revision + ", " + api.operations.length + " operations",
          ]);
          if (api.change_log.length) {
            const entries = el("ul", { class: "change-log" }, api.change_log.map((entry) =>
              el("li", {}, [
                el("strong", { text: entry.revision ? "Revision " + entry.revision : entry.release }),
                " — " + entry.notes,
              ]),
            ));
            item.append(el("details", {}, [el("summary", { text: "Change log" }), entries]));
          }
          return item;
        }),
      );
      const actions = el("div", {});
      if (product.require_subscription) {
        const count = state.subscriptions.filter(sub => sub.products.includes(product.id) && ['active', 'submitted'].includes(sub.state)).length;
        const atLimit = product.subscriptions_limit != null && count >= product.subscriptions_limit;
        actions.append(el("button", {
          type: "button",
          class: "secondary",
          disabled: atLimit ? "disabled" : undefined,
          text: atLimit ? "Subscription limit reached" : count ? "Request another subscription" : "Request subscription",
          onclick: () => requestSubscription(product.id, product.terms),
        }));
      }
      root.append(el("div", { class: "product" }, [
        el("h3", {}, [product.name, badges]),
        el("p", { class: "status", text: product.description ?? "" }),
        apis,
        actions,
      ]));
    }
  }

  function renderSubscriptions() {
    const body = document.getElementById("subs-body");
    body.replaceChildren();
    for (const sub of state.subscriptions) {
      body.append(el("tr", {}, [
        el("td", { text: sub.name }),
        el("td", { text: sub.state }),
        el("td", { text: sub.products.join(", ") }),
        el("td", { class: "keys", text: sub.keys.primary }),
      ]));
    }
    if (!state.subscriptions.length) {
      body.append(el("tr", {}, [el("td", { colspan: "4", text: "No subscriptions yet." })]));
    }
    const keySelect = document.getElementById("key-select");
    keySelect.replaceChildren(el("option", { value: "", text: "none" }));
    for (const sub of state.subscriptions) {
      keySelect.append(el("option", { value: sub.keys.primary, text: sub.name + " (" + sub.state + ")" }));
    }
  }

  function renderOperations() {
    const select = document.getElementById("op-select");
    select.replaceChildren();
    for (const operation of state.selectedApi?.operations ?? []) {
      const option = el("option", {value: operation.id, text: operation.method + " " + operation.request_url});
      option.request = operation;
      select.append(option);
    }
    const updatePath = () => { document.getElementById("try-path").value = select.selectedOptions[0]?.request.request_url ?? ''; };
    document.getElementById('try-send').disabled = select.options.length === 0;
    updatePath(); select.onchange = updatePath;
    void renderReference();
  }

  function renderApiVersions() {
    const apiSelect = document.getElementById('api-select');
    const versionSelect = document.getElementById('version-select');
    state.apiGroups = new Map();
    for (const product of state.catalog?.products ?? []) {
      for (const api of product.apis) {
        const groupId = api.api_version_set ? 'version-set:' + api.api_version_set : 'api:' + api.id;
        if (!state.apiGroups.has(groupId)) state.apiGroups.set(groupId, []);
        const group = state.apiGroups.get(groupId);
        if (!group.some(item => item.id === api.id)) group.push(api);
      }
    }
    apiSelect.replaceChildren(...Array.from(state.apiGroups, ([id, group]) => el('option', {value:id, text:group[0].version_set_name ?? group[0].name})));
    const chooseVersion = () => { state.selectedApi = (state.apiGroups.get(apiSelect.value) ?? []).find(api => api.id === versionSelect.value); renderOperations(); };
    const chooseApi = () => {
      const group = state.apiGroups.get(apiSelect.value) ?? [];
      versionSelect.replaceChildren(...group.map(api => el('option', {value:api.id, text:api.api_version ?? 'Original'})));
      chooseVersion();
    };
    apiSelect.disabled = !state.apiGroups.size; versionSelect.disabled = !state.apiGroups.size;
    apiSelect.onchange = chooseApi; versionSelect.onchange = chooseVersion; chooseApi();
  }

  async function refresh() {
    const status = document.getElementById("user-status");
    const generation = ++state.identityGeneration;
    clearPortalData();
    try {
      const [catalog, subs] = await Promise.all([
        fetchJson("/apim/portal/catalog"),
        fetchJson("/apim/portal/subscriptions"),
      ]);
      if (generation !== state.identityGeneration) return;
      state.catalog = catalog;
      state.subscriptions = subs.subscriptions;
      status.textContent = "";
      renderCatalog();
      renderSubscriptions();
      renderApiVersions();
    } catch (error) {
      if (generation === state.identityGeneration) showIdentityError(error);
    }
  }

  async function requestSubscription(productId, terms) {
    const status = document.getElementById("user-status");
    try {
      if (terms && !window.confirm(terms + "\n\nAccept these terms and request a subscription?")) return;
      await fetchJson("/apim/portal/subscriptions", {
        method: "POST",
        body: JSON.stringify({ product_id: productId, accept_terms: Boolean(terms) }),
      });
      await refresh();
    } catch (error) {
      status.textContent = String(error.message ?? error);
    }
  }

  async function tryIt() {
    const path = document.getElementById("try-path").value;
    const key = document.getElementById("key-select").value;
    const operation = document.getElementById("op-select").selectedOptions[0]?.request;
    const method = operation?.method ?? "GET";
    const status = document.getElementById("try-status");
    const output = document.getElementById("try-output");
    status.textContent = "Calling " + method + " " + path + " ...";
    try {
      const requestHeaders = {...(operation?.request_headers ?? {})};
      if (key) requestHeaders["Ocp-Apim-Subscription-Key"] = key;
      const response = await fetch(path, { method, headers: requestHeaders });
      const text = await response.text();
      status.textContent = response.status + " " + response.statusText;
      try { output.textContent = JSON.stringify(JSON.parse(text), null, 2); }
      catch { output.textContent = text || "(empty body)"; }
    } catch (error) {
      status.textContent = String(error.message ?? error);
    }
  }

  async function boot() {
    const generation = ++state.identityGeneration;
    clearPortalData();
    const select = document.getElementById("user-select");
    const payload = await fetchJson("/apim/portal/users");
    if (generation !== state.identityGeneration) return;
    const signed = payload.authentication === 'signed-jwt';
    document.getElementById('portal-sign-out').hidden = !signed;
    document.getElementById('user-label').textContent = signed ? 'Verified user' : 'Demo user';
    document.getElementById('identity-description').textContent = signed
      ? 'Your portal access is verified by the supplied token.' : 'Local demo access uses the selected configured user. No sign-in is required.';
    document.getElementById('identity-status').textContent = signed ? 'Token identity verified.' : 'Demo access';
    select.disabled = signed;
    select.replaceChildren();
    for (const user of payload.users) {
      select.append(el("option", { value: user.id, text: user.name + " (" + user.id + ")" }));
    }
    if (!payload.users.length) {
      document.getElementById("user-status").textContent =
        "No users are defined in the simulator config, so the portal has no one to act as.";
      return;
    }
    state.user = select.value;
    select.onchange = () => { state.user = select.value; void refresh(); };
    document.getElementById("try-send").onclick = () => void tryIt();
    await refresh();
  }

  document.getElementById("portal-sign-in").onclick = () => {
    if (!document.getElementById('portal-token').value.trim()) return;
    void boot().catch(showIdentityError);
  };
  document.getElementById('portal-sign-out').onclick = () => {
    ++state.identityGeneration;
    state.user = '';
    document.getElementById('portal-token').value = '';
    document.getElementById('user-select').replaceChildren();
    document.getElementById('portal-sign-out').hidden = true;
    document.getElementById('user-status').textContent = '';
    document.getElementById('user-label').textContent = 'User';
    document.getElementById('identity-status').textContent = 'Signed out';
    document.getElementById('identity-description').textContent = 'Supply a portal token to verify your identity.';
    clearPortalData();
  };
  window.addEventListener('apim-theme-change', () => {
    document.querySelector('#api-reference iframe')?.contentWindow.postMessage(
      {type:'apim-theme', dark:document.documentElement.dataset.theme === 'dark'}, location.origin);
  });
  document.getElementById('key-select').onchange = () => void renderReference();
  document.getElementById('reference-refresh').onclick = () => void renderReference();
  function clearReference() {
    ++state.referenceGeneration;
    document.getElementById('api-reference').replaceChildren();
  }
  function clearPortalData() {
    clearReference();
    state.catalog = null; state.subscriptions = []; state.selectedApi = null;
    renderCatalog(); renderSubscriptions(); renderApiVersions();
    document.getElementById('try-output').textContent = 'Pick an operation and send a request.';
    document.getElementById('try-status').textContent = '';
  }
  function showIdentityError(error) {
    ++state.identityGeneration;
    state.user = "";
    document.getElementById('portal-sign-out').hidden = true;
    document.getElementById('identity-status').textContent = 'No verified identity';
    document.getElementById("user-status").textContent = error.message;
    clearPortalData();
  }
  void boot().catch(showIdentityError);
</script>
</body>
</html>
"""


def _portal_image_url(site: PortalSite, url: str) -> str:
    """Inline this snapshot's uploads so private draft previews need no public asset."""
    media = next((item for item in site.media if url == "/apim/portal/media/" + item.id), None)
    return f"data:{media.content_type};base64,{media.content_base64}" if media else url


def render_portal_page(site: PortalSite, *, slug: str = "home") -> str:
    """Render only a selected immutable snapshot; all editable text is escaped."""
    page = next((page for page in site.pages if page.slug == slug), None)
    if page is None:
        raise HTTPException(status_code=404, detail="Portal page not found")
    links = " ".join(
        f'<a href="/apim/portal{("/pages/" + item.slug) if item.slug != "home" else ""}">{escape(item.title)}</a>'
        for item in site.pages
    )
    logo = (
        f'<img src="{escape(_portal_image_url(site, site.logo_url), quote=True)}" alt="" width="120">'
        if site.logo_url
        else ""
    )
    navigation = f'<nav aria-label="Portal pages">{logo}{links}</nav>' if len(site.pages) > 1 or logo else ""
    content = f'<h1>{escape(page.title)}</h1><p class="lede" style="white-space:pre-wrap">{escape(page.content)}</p>'
    html = PORTAL_HTML.replace(
        "<title>APIM Simulator Developer Portal</title>",
        f"<title>APIM Simulator - developer portal | {escape(site.site_title)}</title>",
    )
    start = html.index("  <h1>Developer Portal</h1>")
    end = html.index("  <section>", start)
    html = html[:start] + navigation + content + html[end:]
    # Additional pages share branding and navigation, while the home page retains
    # the discovery, subscription, and try-it widgets.
    if slug != "home":
        html = html[: html.index("  <section>")] + "</main></body></html>"
    dark = "--panel:#242424;--ink:#f4efe4;--muted:#d4cfc4;--line:#777;" if site.theme == "dark" else ""
    background = (
        "#181818"
        if site.theme == "dark" and site.background_color == "#f2ede1"
        else ("#f3f2f1" if site.background_color == "#f2ede1" else site.background_color)
    )
    image = ""
    if site.background_image_url:
        # HTML escaping alone cannot quote a CSS URL: escape CSS delimiters first.
        css_url = (
            _portal_image_url(site, site.background_image_url)
            .replace("'", "%27")
            .replace('"', "%22")
            .replace("<", "%3C")
            .replace(">", "%3E")
        )
        image = f"body{{background-image:url('{css_url}');background-size:cover}}"
    style = (
        f"<style>:root{{color-scheme:{site.theme};--accent:{site.accent_color};--bg:{background};{dark}}}"
        f"nav{{display:flex;align-items:center;gap:1rem;flex-wrap:wrap;margin-bottom:1.5rem}}"
        f"nav a{{color:var(--accent)}}.masthead nav a{{color:white}}{image}</style>"
    )
    return html.replace(
        "__OPERATOR_URL__", escape(os.getenv("OPERATOR_CONSOLE_URL", "http://localhost:3007"), quote=True)
    ).replace("</head>", style + "</head>")
