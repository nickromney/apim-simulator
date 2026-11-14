// The parent owns portal identity. Only the contract and selected gateway key
// enter this frame; neither is saved to browser storage.
let reference;
let darkMode;

function applyAppearance() {
  document.body.classList.toggle('dark-mode', darkMode);
  document.body.classList.toggle('light-mode', !darkMode);
}

// Scalar captures the forced mode at mount and can reapply it on media changes.
// Keep the portal's choice without remounting the request editor.
const appearanceObserver = new MutationObserver(applyAppearance);

window.addEventListener('message', (event) => {
  if (event.origin !== location.origin || event.source !== window.parent) return;
  if (event.data?.type === 'apim-theme') {
    if (reference) {
      darkMode = event.data.dark;
      reference.updateConfiguration({ ...reference.getConfiguration(), darkMode });
      applyAppearance();
    }
    return;
  }
  if (event.data?.type !== 'apim-reference') return;
  const { document: contract, key, dark } = event.data;
  const securitySchemes = Object.fromEntries(Object.entries(contract.components?.securitySchemes ?? {})
    .map(([id, scheme]) => [id, { ...scheme, value: key || '' }]));
  darkMode = dark;
  appearanceObserver.observe(document.body, { attributes: true, attributeFilter: ['class'] });
  reference = Scalar.createApiReference('#reference', {
    forceDarkModeState: dark ? 'dark' : 'light',
    darkMode: dark,
    content: contract,
    authentication: { preferredSecurityScheme: Object.keys(securitySchemes), securitySchemes },
    servers: contract.servers.map((server) => ({ ...server, url: new URL(server.url, location.origin).href })),
    withDefaultFonts: false,
    telemetry: false,
    persistAuth: false,
    agent: { disabled: true },
    showDeveloperTools: 'never',
    // Same-origin calls only, including when editing the URL in the API client.
    customFetch: (input, init) => {
      const url = new URL(input instanceof Request ? input.url : input, location.origin);
      if (url.origin !== location.origin) return Promise.reject(new Error('Use the local gateway URL.'));
      return fetch(input, { ...init, credentials: 'omit' });
    },
  });
});
