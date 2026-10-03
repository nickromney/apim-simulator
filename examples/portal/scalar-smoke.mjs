import assert from 'node:assert/strict';
import { chromium, expect } from '@playwright/test';

const baseURL = (process.env.APIM_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome' });
const context = await browser.newContext();
const externalRequests = [];
await context.route('**/*', async (route) => {
  if (new URL(route.request().url()).origin !== new URL(baseURL).origin) {
    externalRequests.push(route.request().url());
    await route.abort();
  } else await route.continue();
});
const page = await context.newPage();
const errors = [];
page.on('pageerror', (error) => errors.push(error.message));
try {
  await page.goto(baseURL + '/apim/portal');
  await expect(page.locator('#key-select option')).toHaveCount(2);
  await page.locator('#version-select').selectOption('echo-v2');
  await page.locator('#key-select').selectOption('scalar-demo-key');
  const reference = page.frameLocator('#api-reference iframe');
  await reference.getByRole('heading', { name: 'Echo v2', exact: true }).waitFor();
  await reference.getByRole('button', { name: /Test Request/ }).first().click();
  const waitForCall = () => page.waitForResponse((response) => response.url() === baseURL + '/echo/v2/json'
    && response.request().method() === 'POST');
  const firstResponse = waitForCall();
  await reference.getByRole('button', { name: /Send post request/ }).first().click();
  const first = await firstResponse;
  assert.equal(first.status(), 200);
  assert.equal(first.request().headers()['x-echo-key'], 'scalar-demo-key');
  assert.equal(first.request().headers().authorization, undefined);
  assert.deepEqual(await first.json(), { message: 'hello v2' });
  await reference.locator('.cm-content[contenteditable="true"]').first().fill('{"message":"edited locally"}');
  const editedResponse = waitForCall();
  await reference.getByRole('button', { name: /Send post request/ }).first().click();
  assert.deepEqual(await (await editedResponse).json(), { message: 'edited locally' });
  assert.ok(!JSON.stringify(await context.storageState({ indexedDB: true })).includes('scalar-demo-key'));
  for (const frame of page.frames()) {
    assert.ok(!(await frame.evaluate(() => JSON.stringify(sessionStorage))).includes('scalar-demo-key'));
  }
  if (process.env.SCALAR_SCREENSHOT) await page.screenshot({ path: process.env.SCALAR_SCREENSHOT, fullPage: true });
  await page.locator('#user-select').selectOption('other-dev');
  await expect(page.locator('#key-select option')).toHaveCount(1);
  await reference.getByRole('heading', { name: 'Echo v1', exact: true }).waitFor();
  assert.ok(!(await reference.locator('body').innerText()).includes('scalar-demo-key'));
  assert.deepEqual(externalRequests, []);
  assert.deepEqual(errors, []);
  console.log('PASS: offline Scalar rendering, version selection, custom subscription header, POST example, edited JSON body, no persisted key and identity reset');
} finally {
  await browser.close();
}
