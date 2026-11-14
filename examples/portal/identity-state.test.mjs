import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../../app/portal.py', import.meta.url), 'utf8');

test('catalog requests use the configured demo identity header or the signed token', async () => {
  const token = { value: '' };
  const requests = [];
  const context = vm.createContext({
    state: { user: 'developer' },
    document: {
      getElementById: () => token,
      querySelector: () => ({ dataset: { portalUserHeader: 'X-Local-Developer' } }),
    },
    fetch: async (path, options) => {
      requests.push(new Headers(options.headers));
      return { ok: true, text: async () => '{}' };
    },
  });
  vm.runInContext(source.slice(source.indexOf('  function headers()'), source.indexOf('  function el(')), context);
  await context.fetchJson('/apim/portal/catalog');
  token.value = 'signed-token';
  await context.fetchJson('/apim/portal/catalog');
  assert.equal(requests[0].get('X-Local-Developer'), 'developer');
  assert.equal(requests[1].get('X-Local-Developer'), null);
  assert.equal(requests[1].get('Authorization'), 'Bearer signed-token');
});

function harness(name, nextName) {
  const nodes = {
    'try-path': { value: '/private' }, 'key-select': { value: 'key' },
    'op-select': { selectedOptions: [{ request: { method: 'GET' } }] },
    'try-status': { textContent: '' }, 'try-output': { textContent: '' },
    'user-status': { textContent: '' }, 'user-select': {},
  };
  let resolve, reject;
  const pending = new Promise((yes, no) => { resolve = yes; reject = no; });
  let refreshes = 0;
  const context = vm.createContext({
    state: { identityGeneration: 0 }, document: { getElementById: id => nodes[id] },
    URL, location: new URL('http://portal.example.test/apim/portal'),
    fetch: () => pending, fetchJson: () => pending, clearPortalData: () => {},
    refresh: async () => { refreshes++; },
  });
  const start = source.indexOf(`  async function ${name}(`);
  const end = source.indexOf(nextName, start);
  assert.ok(start >= 0 && end > start);
  vm.runInContext(source.slice(start, end), context);
  return { context, nodes, resolve, reject, refreshes: () => refreshes };
}

for (const path of ['https://external.example.test/test', '//external.example.test/test']) {
  test(`quick check refuses to send a subscription key to ${path}`, async () => {
    const h = harness('tryIt', '\n  async function boot(');
    h.nodes['try-path'].value = path;
    let requested = false;
    h.context.fetch = async () => {
      requested = true;
      return { status: 200, statusText: 'OK', text: async () => '' };
    };
    await h.context.tryIt();
    assert.equal(requested, false);
    assert.match(h.nodes['try-status'].textContent, /same gateway/);
  });
}

test('quick check sends the key to the gateway and disables redirects', async () => {
  const h = harness('tryIt', '\n  async function boot(');
  let request;
  h.context.fetch = async (url, options) => {
    request = { url, options };
    return { status: 200, statusText: 'OK', text: async () => 'gateway response' };
  };
  await h.context.tryIt();
  assert.equal(request.url.href, 'http://portal.example.test/private');
  assert.equal(request.options.headers['Ocp-Apim-Subscription-Key'], 'key');
  assert.equal(request.options.redirect, 'error');
  assert.equal(h.nodes['try-output'].textContent, 'gateway response');
});

for (const outcome of ['response', 'error']) {
  test(`quick check discards stale ${outcome} after identity changes`, async () => {
    const h = harness('tryIt', '\n  async function boot(');
    const call = h.context.tryIt();
    h.context.state.identityGeneration++;
    h.nodes['try-output'].textContent = 'Cleared';
    h.nodes['try-status'].textContent = '';
    if (outcome === 'response') h.resolve({ status: 200, statusText: 'OK', text: async () => 'private response' });
    else h.reject(new Error('previous identity error'));
    await call;
    assert.equal(h.nodes['try-output'].textContent, 'Cleared');
    assert.equal(h.nodes['try-status'].textContent, '');
  });
}

test('stale subscription completion does not refresh a signed-out session', async () => {
  const h = harness('requestSubscription', '\n  async function tryIt(');
  const call = h.context.requestSubscription('product', '');
  h.context.state.identityGeneration++;
  h.resolve({});
  await call;
  assert.equal(h.refreshes(), 0);
});

test('stale sign-in rejection does not clear a newer identity', async () => {
  const h = harness('boot', '\n  document.getElementById("portal-sign-in")');
  const call = h.context.boot();
  h.context.state.identityGeneration++;
  h.reject(new Error('old sign-in failed'));
  await assert.doesNotReject(call);
});
