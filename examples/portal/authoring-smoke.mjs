import assert from "node:assert/strict";
import { chromium, expect } from "@playwright/test";

const gateway = (process.env.APIM_BASE_URL || "http://localhost:8000").replace(/\/$/, "");
const consoleURL = (process.env.OPERATOR_CONSOLE_BASE_URL || "http://localhost:3007").replace(/\/$/, "");
const tenant = process.env.APIM_TENANT_KEY || "local-dev-tenant-key";
const id = `smoke-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
const product = `${id}-product`;
const version = `${id}-v2`;
const versionSet = `${id}-versions`;
let subscription = null;
const headers = { "X-Apim-Tenant-Key": tenant, "Content-Type": "application/json" };
const visitorHeaders = { "X-Apim-Portal-User": "demo-dev", "Content-Type": "application/json" };
const errors = [];
const browser = await chromium.launch({
  headless: true,
  channel: process.env.PLAYWRIGHT_CHANNEL || "chrome",
});
const page = await browser.newPage();
page.on("pageerror", (error) => errors.push(error.message));
async function api(path, options = {}) {
  const response = await fetch(gateway + path, options);
  if (!response.ok) throw new Error(`${options.method || "GET"} ${path}: ${response.status} ${await response.text()}`);
  return response.json();
}
async function catalog() {
  return api("/apim/portal/catalog", { headers: visitorHeaders });
}
async function mockCall(path, key) {
  const response = await fetch(gateway + path, { headers: key ? { "Ocp-Apim-Subscription-Key": key } : {} });
  return { status: response.status, body: await response.json() };
}
try {
  await page.goto(consoleURL);
  if (!(await page.getByRole("button", { name: "Load Local Demo" }).isVisible())) {
    await page.locator(".connection-control summary").click();
  }
  await page.getByRole("button", { name: "Load Local Demo" }).click();
  await page.getByLabel("Gateway base URL").fill(gateway);
  await page.getByLabel("Tenant key", { exact: true }).fill(tenant);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByRole("heading", { name: "API Explorer" })).toBeVisible();
  await page.getByRole("button", { name: "Add API", exact: true }).click();
  await page.getByRole("button", { name: /HTTP Set an API suffix/ }).click();
  const settings = page.locator("form").filter({ has: page.getByRole("heading", { name: "API settings" }) });
  await settings.getByLabel("Name (resource ID)").fill(id);
  await settings.getByLabel("Display name").fill(id);
  await settings.getByLabel("API URL suffix").fill(id);
  await settings.getByLabel("Web service URL").fill("http://127.0.0.1:1");
  await settings.getByRole("button", { name: "Create API" }).click();
  const operation = page.locator(".operation-form");
  await operation.getByRole("button", { name: "Add operation" }).click();
  await operation.getByLabel("Operation ID").fill("hello");
  await operation.getByLabel("Display name").fill("Hello");
  await operation.getByLabel("URL template").fill("/hello");
  await operation.getByRole("button", { name: "Create", exact: true }).click();
  const editor = page.locator(".mock-response-editor");
  await expect(editor.getByLabel("Example body")).toBeEnabled();
  await editor.getByLabel("Status code").fill("201");
  await editor.getByLabel("Example body").fill("{bad");
  await editor.getByRole("button", { name: "Save response example" }).click();
  await expect(editor.getByText(/valid JSON/i)).toBeVisible();
  await editor.getByLabel("Example body").fill('{"message":"hello smoke"}');
  await editor.getByRole("button", { name: "Save response example" }).click();
  await expect(editor.getByText(/saved/i).first()).toBeVisible();
  await editor.getByRole("button", { name: "Enable mocking" }).click();
  await expect(editor.getByText("Mocking is enabled on this operation.")).toBeVisible();
  await page.getByRole("button", { name: "Test", exact: true }).click();
  await page.getByRole("button", { name: "Send request" }).click();
  await expect(page.locator(".response-heading")).toContainText("201");
  await expect(page.locator(".response-split")).toContainText("hello smoke");
  console.log("Mock example: 201 and expected body.");
  await page.getByRole("button", { name: "Products", exact: true }).click();
  const products = page.locator(".products-panel");
  await products.getByLabel("Product ID").fill(product);
  await products.getByLabel("Name", { exact: true }).fill(product);
  await products.getByLabel("Require approval").check();
  await products.getByRole("button", { name: "Save product" }).click();
  await expect(products.getByText(`Saved product ${product}.`)).toBeVisible();
  await products.getByLabel("API", { exact: true }).selectOption(id);
  await products.getByRole("button", { name: "Add API", exact: true }).click();
  await expect(products.getByText(`Added API ${id} to product ${product}.`)).toBeVisible();
  assert.equal(
    (await catalog()).products.some((p) => p.id === product),
    false,
  );
  await products.getByLabel("Publication").selectOption("published");
  await products.getByRole("button", { name: "Save product" }).click();
  await expect(products.getByText(`Saved product ${product}.`)).toBeVisible();
  assert.equal(
    (await catalog()).products.some((p) => p.id === product),
    true,
  );
  console.log("Product: hidden before publication, visible after publication.");
  const sub = await api("/apim/portal/subscriptions", {
    method: "POST",
    headers: visitorHeaders,
    body: JSON.stringify({ product_id: product }),
  });
  subscription = sub.id;
  assert.equal(sub.state, "submitted");
  assert.equal((await mockCall(`/${id}/hello`, sub.keys.primary)).status, 401);
  await page.getByRole("button", { name: "Subscriptions", exact: true }).click();
  const card = page.locator(".subscription-card").filter({ hasText: subscription });
  await card.getByRole("button", { name: "Approve" }).click();
  await expect(card.locator(".state-pill")).toHaveText("active");
  assert.deepEqual(await mockCall(`/${id}/hello`, sub.keys.primary), {
    status: 201,
    body: { message: "hello smoke" },
  });
  console.log("Subscription: pending key rejected, approved key accepted.");
  await page.getByRole("button", { name: "APIs", exact: true }).click();
  await page.locator(".tree-api").filter({ hasText: id }).getByRole("button").click();
  await page.getByRole("button", { name: "Versions", exact: true }).click();
  await page.getByLabel("Version API ID").fill(version);
  await page.getByLabel("Version label").fill("v2");
  await page.getByLabel("Version set ID").fill(versionSet);
  const versionResponse = page.waitForResponse(
    (response) => response.url().endsWith(`/apis/${id}/versions`) && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Create version", exact: true }).click();
  assert.equal((await versionResponse).status(), 201);
  const cloned = await api(`/apim/management/apis/${version}`, { headers });
  assert.ok(cloned.products.includes(product));
  assert.deepEqual(await mockCall(`/${id}/hello`, sub.keys.primary), {
    status: 201,
    body: { message: "hello smoke" },
  });
  assert.deepEqual(await mockCall(`/${id}/v2/hello`, sub.keys.primary), {
    status: 201,
    body: { message: "hello smoke" },
  });
  console.log("Version: original and v2 returned the mock response.");

  await page
    .locator(".tree-api")
    .filter({ hasText: `/${id} · Original` })
    .getByRole("button")
    .click();
  await page.locator(".operation-browser").getByRole("button", { name: /Hello/ }).click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(editor.getByLabel("Example body")).toHaveValue(/hello smoke/);
  await editor.getByLabel("Example body").fill('{"message":"unsaved draft"}');
  if (!(await page.getByRole("button", { name: "Connect", exact: true }).isVisible())) {
    await page.locator(".connection-control summary").click();
  }
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByLabel("Gateway base URL").fill(`${gateway}/`);
  await expect(page.getByRole("heading", { name: "API Explorer" })).toBeHidden();
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByRole("heading", { name: "API Explorer" })).toBeVisible();
  await page
    .locator(".tree-api")
    .filter({ hasText: `/${id} · Original` })
    .getByRole("button")
    .click();
  await page.locator(".operation-browser").getByRole("button", { name: /Hello/ }).click();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(editor.getByLabel("Example body")).toHaveValue(/hello smoke/);
  console.log("Reconnect: discarded unsaved mock draft and reloaded saved example.");
  assert.deepEqual(errors, []);
  console.log("Authoring smoke passed: mock, product approval, and version.");
} finally {
  for (const path of [
    ...(subscription ? [`/apim/management/subscriptions/${subscription}`] : []),
    `/apim/management/apis/${version}`,
    `/apim/management/apis/${id}`,
    `/apim/management/products/${product}`,
    `/apim/management/api-version-sets/${versionSet}`,
  ]) {
    const response = await fetch(gateway + path, { method: "DELETE", headers }).catch(() => null);
    if (!response || (!response.ok && response.status !== 404))
      console.error(`Cleanup failed: ${path} (${response?.status ?? "network error"})`);
  }
  await browser.close();
}
