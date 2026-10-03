// The parent owns portal identity. Only the contract and selected gateway key
// enter this frame; neither is saved to browser storage.
let reference;
let configuration;
window.addEventListener('message', (event) => {
  if (event.origin !== location.origin || event.source !== window.parent) return;
  if (event.data?.type === 'apim-theme') { reference?.updateConfiguration({...configuration, forceDarkModeState:event.data.dark ? 'dark' : 'light'}); return; }
  if (event.data?.type !== 'apim-reference') return;
  const { document, key, dark } = event.data;
  const securitySchemes = Object.fromEntries(Object.entries(document.components?.securitySchemes ?? {})
    .map(([id, scheme]) => [id, { ...scheme, value: key || '' }]));
  configuration = {
    forceDarkModeState: dark ? 'dark' : 'light',
    content: document,
    authentication: { preferredSecurityScheme: Object.keys(securitySchemes), securitySchemes },
    servers: document.servers.map((server) => ({ ...server, url: new URL(server.url, location.origin).href })),
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
  };
  reference = Scalar.createApiReference('#reference', configuration);
}, { once: false });
