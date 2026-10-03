// Regression for GHSA-wrjc-x8rr-h8h6. Upstream MIT fix: remix-run/react-router#15176.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { createRoot } = require('react-dom/client');
const { JSDOM } = require('jsdom');
const {
  MemoryRouter,
  Link,
  BrowserRouter,
  useNavigate,
} = require('react-router-dom');
const { resolvePath } = require('@remix-run/router');
const origin = 'https://portal.example.test';
const mixed = [
  '\\\\attacker.example.test/path',
  '/\\attacker.example.test/path',
  '\\/attacker.example.test/path',
];

for (const path of mixed) {
  test(`mixed separators remain internal in resolvePath and Link: ${JSON.stringify(
    path,
  )}`, () => {
    assert.equal(resolvePath(path).pathname, '/attacker.example.test/path');
    const html = renderToStaticMarkup(
      React.createElement(
        MemoryRouter,
        null,
        React.createElement(Link, { to: path }, 'catalog link'),
      ),
    );
    const anchor = new JSDOM(html, {
      url: origin,
    }).window.document.querySelector('a');
    assert.equal(new URL(anchor.href).origin, origin);
    assert.equal(anchor.getAttribute('href'), '/attacker.example.test/path');
  });
}

test('useNavigate mixed paths never invokes external location fallback', async () => {
  const dom = new JSDOM('<div id="root"></div>', { url: origin + '/home' });
  global.window = dom.window;
  global.document = dom.window.document;
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const root = createRoot(document.getElementById('root'));
  let navigate;
  function Probe() {
    navigate = useNavigate();
    return null;
  }
  try {
    await React.act(async () =>
      root.render(
        React.createElement(BrowserRouter, null, React.createElement(Probe)),
      ),
    );
    for (const path of mixed) {
      await React.act(async () => navigate(path));
      assert.equal(window.location.origin, origin);
      assert.equal(window.location.pathname, '/attacker.example.test/path');
    }
  } finally {
    await React.act(async () => root.unmount());
    dom.window.close();
    delete global.window;
    delete global.document;
    delete global.IS_REACT_ACT_ENVIRONMENT;
  }
});

test('ordinary paths and intentionally explicit external links retain their behavior', () => {
  assert.equal(
    resolvePath('../catalog/item', '/docs/current').pathname,
    '/docs/catalog/item',
  );
  const html = renderToStaticMarkup(
    React.createElement(
      MemoryRouter,
      null,
      React.createElement(
        Link,
        { to: 'https://external.example.test/document' },
        'external docs',
      ),
    ),
  );
  assert.equal(
    new JSDOM(html).window.document.querySelector('a').href,
    'https://external.example.test/document',
  );
});
