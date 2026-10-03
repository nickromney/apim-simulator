import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../../app/portal.py', import.meta.url), 'utf8');
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
    fetch: () => pending, fetchJson: () => pending, clearPortalData: () => {},
    refresh: async () => { refreshes++; },
  });
  const start = source.indexOf(`  async function ${name}(`);
  const end = source.indexOf(nextName, start);
  assert.ok(start >= 0 && end > start);
  vm.runInContext(source.slice(start, end), context);
  return { context, nodes, resolve, reject, refreshes: () => refreshes };
}

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
