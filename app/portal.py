"""Consumer-facing developer-portal workflows.

This is the adapted local equivalent of the Azure developer portal's consumer
surface: browse published products, inspect API operations, request a
subscription, and try calls with a key. Identity is simulator-grade — the
acting user is a config-defined user passed in a header, not a signed-in
account. The portal CMS, theming, email, and notification surface stay out of
scope.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.config import (
    ApiConfig,
    GatewayConfig,
    ProductConfig,
    ProductState,
    Subscription,
    SubscriptionKeyPair,
    SubscriptionState,
    UserConfig,
)


def require_portal_user(cfg: GatewayConfig, user_id: str | None) -> UserConfig:
    if not user_id:
        raise HTTPException(status_code=401, detail="Portal user header is required")
    user = cfg.users.get(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Unknown portal user")
    if user.state and user.state.lower() != "active":
        raise HTTPException(status_code=403, detail=f"Portal user is not active (state: {user.state})")
    return user


def user_group_ids(cfg: GatewayConfig, user_id: str) -> set[str]:
    return {group_id for group_id, group in cfg.groups.items() if user_id in group.users}


def product_visible(product: ProductConfig, groups: set[str]) -> bool:
    if product.state != ProductState.Published:
        return False
    # Products with no group links are visible to every portal user. Azure
    # scopes visibility through built-in groups instead; documented as adapted.
    if not product.groups:
        return True
    return bool(set(product.groups) & groups)


def _project_portal_api(api_id: str, api: ApiConfig) -> dict[str, Any]:
    return {
        "id": api_id,
        "name": api.name,
        "path": api.path,
        "api_version": api.api_version,
        "revision": api.revision,
        "revision_description": api.revision_description,
        "operations": [
            {
                "id": operation_id,
                "name": operation.name,
                "method": operation.method,
                "url_template": operation.url_template,
                "description": operation.description,
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
        apis = [_project_portal_api(api_id, api) for api_id, api in cfg.apis.items() if product_id in api.products]
        products.append(
            {
                "id": product_id,
                "name": product.name,
                "description": product.description,
                "require_subscription": product.require_subscription,
                "approval_required": product.approval_required,
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
    cfg: GatewayConfig, user_id: str, product_id: str, display_name: str | None = None
) -> Subscription:
    groups = user_group_ids(cfg, user_id)
    product = cfg.products.get(product_id)
    if product is None or not product_visible(product, groups):
        raise HTTPException(status_code=404, detail="Product not found")
    if not product.require_subscription:
        raise HTTPException(status_code=400, detail="Product does not use subscriptions")
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
    if any(subscription.id == sub_id for subscription in cfg.subscription.subscriptions.values()):
        raise HTTPException(status_code=409, detail="Subscription already exists for this product")

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


PORTAL_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>APIM Simulator Developer Portal</title>
<style>
  :root {
    color-scheme: light;
    --bg: #f2ede1;
    --panel: rgba(255, 250, 242, 0.94);
    --ink: #1a1711;
    --muted: #655f54;
    --line: rgba(26, 23, 17, 0.18);
    --accent: #12705f;
    --accent-soft: rgba(18, 112, 95, 0.14);
    --warn-soft: rgba(190, 93, 38, 0.2);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: "Helvetica Neue", Arial, sans-serif;
    color: var(--ink);
    background: radial-gradient(circle at top left, rgba(18, 112, 95, 0.14), transparent 34%), var(--bg);
    min-height: 100vh;
  }
  main { max-width: 960px; margin: 0 auto; padding: 2rem 1.25rem 4rem; }
  h1 { font-size: 1.7rem; margin: 0; }
  .lede { color: var(--muted); margin: 0.4rem 0 1.6rem; }
  section {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 16px;
    padding: 1.2rem 1.4rem;
    margin-bottom: 1.4rem;
  }
  h2 { margin: 0 0 0.8rem; font-size: 1.15rem; }
  label { display: inline-flex; flex-direction: column; gap: 0.3rem; font-size: 0.85rem; color: var(--muted); }
  select, input, button {
    font: inherit;
    padding: 0.45rem 0.7rem;
    border-radius: 8px;
    border: 1px solid var(--line);
    background: #fff;
  }
  button { cursor: pointer; background: var(--accent); color: #fff; border: none; }
  button.secondary { background: transparent; color: var(--accent); border: 1px solid var(--accent); }
  button:disabled { opacity: 0.5; cursor: default; }
  .product { border-top: 1px solid var(--line); padding: 0.9rem 0; }
  .product:first-of-type { border-top: none; }
  .product h3 { margin: 0; }
  .badges { display: inline-flex; gap: 0.4rem; margin-left: 0.6rem; vertical-align: middle; }
  .badge {
    font-size: 0.72rem;
    padding: 0.15rem 0.55rem;
    border-radius: 999px;
    background: var(--accent-soft);
  }
  .badge.warn { background: var(--warn-soft); }
  .apis { margin: 0.5rem 0 0.7rem; padding-left: 1.1rem; color: var(--muted); font-size: 0.9rem; }
  .apis code { color: var(--ink); }
  table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
  th, td { text-align: left; padding: 0.45rem 0.6rem; border-bottom: 1px solid var(--line); }
  td.keys { font-family: monospace; font-size: 0.8rem; word-break: break-all; }
  .tryit-grid { display: grid; gap: 0.7rem; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); align-items: end; }
  pre { background: #171410; color: #f4efe4; padding: 0.9rem; border-radius: 10px; overflow: auto; font-size: 0.82rem; }
  .status { color: var(--muted); font-size: 0.9rem; min-height: 1.2rem; margin: 0.6rem 0 0; }
</style>
</head>
<body>
<main>
  <h1>Developer Portal</h1>
  <p class="lede">
    Browse published products, request a subscription, and try API calls against the local simulator.
    This is the adapted local stand-in for the Azure developer portal's consumer workflows.
  </p>

  <section>
    <h2>Acting user</h2>
    <label>Signed in as
      <select id="user-select"></select>
    </label>
    <p class="status" id="user-status"></p>
  </section>

  <section>
    <h2>Product catalog</h2>
    <div id="catalog"></div>
  </section>

  <section>
    <h2>My subscriptions</h2>
    <table>
      <thead><tr><th>Name</th><th>State</th><th>Products</th><th>Primary key</th></tr></thead>
      <tbody id="subs-body"></tbody>
    </table>
  </section>

  <section>
    <h2>Try it</h2>
    <div class="tryit-grid">
      <label>Operation
        <select id="op-select"></select>
      </label>
      <label>Path
        <input id="try-path" placeholder="/hello/greet" />
      </label>
      <label>Subscription key
        <select id="key-select"></select>
      </label>
      <button id="try-send" type="button">Send</button>
    </div>
    <p class="status" id="try-status"></p>
    <pre id="try-output">Pick an operation and send a request.</pre>
  </section>
</main>

<script>
  const state = { user: "", catalog: null, subscriptions: [] };

  function headers() {
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
    const subscribedProducts = new Set(state.subscriptions.flatMap((sub) => sub.products));
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
          return item;
        }),
      );
      const actions = el("div", {});
      if (product.require_subscription) {
        const subscribed = subscribedProducts.has(product.id);
        actions.append(el("button", {
          type: "button",
          class: "secondary",
          disabled: subscribed ? "disabled" : undefined,
          text: subscribed ? "Subscription requested" : "Request subscription",
          onclick: () => requestSubscription(product.id),
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
    for (const product of state.catalog?.products ?? []) {
      for (const api of product.apis) {
        for (const operation of api.operations) {
          const path = "/" + api.path + operation.url_template;
          select.append(el("option", { value: path, text: operation.method + " " + path }));
        }
      }
    }
    if (select.options.length) {
      document.getElementById("try-path").value = select.value;
    }
    select.onchange = () => { document.getElementById("try-path").value = select.value; };
  }

  async function refresh() {
    const status = document.getElementById("user-status");
    try {
      const [catalog, subs] = await Promise.all([
        fetchJson("/apim/portal/catalog"),
        fetchJson("/apim/portal/subscriptions"),
      ]);
      state.catalog = catalog;
      state.subscriptions = subs.subscriptions;
      status.textContent = "";
      renderCatalog();
      renderSubscriptions();
      renderOperations();
    } catch (error) {
      status.textContent = String(error.message ?? error);
    }
  }

  async function requestSubscription(productId) {
    const status = document.getElementById("user-status");
    try {
      await fetchJson("/apim/portal/subscriptions", {
        method: "POST",
        body: JSON.stringify({ product_id: productId }),
      });
      await refresh();
    } catch (error) {
      status.textContent = String(error.message ?? error);
    }
  }

  async function tryIt() {
    const path = document.getElementById("try-path").value;
    const key = document.getElementById("key-select").value;
    const method = document.getElementById("op-select").selectedOptions[0]?.text.split(" ")[0] ?? "GET";
    const status = document.getElementById("try-status");
    const output = document.getElementById("try-output");
    status.textContent = "Calling " + method + " " + path + " ...";
    try {
      const requestHeaders = {};
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
    const select = document.getElementById("user-select");
    const payload = await (await fetch("/apim/portal/users")).json();
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

  void boot();
</script>
</body>
</html>
"""
