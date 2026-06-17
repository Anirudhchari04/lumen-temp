// page-bridge.js — runs in the Lumen page's main world (not the extension's isolated world)
// Injected via <script src="chrome-extension://..."> by lumen-bridge.js, which lets us
// bypass strict CSP (this script lives at a chrome-extension:// URL, not inline).
//
// Exposes window.lumenExt — the API Lumen's React app calls to ask the extension to
// read/search/send through the Outlook tab.

(function () {
  if (window.lumenExt) {
    // Already injected (e.g. SPA route change) — refresh marker and bail
    window.lumenExt.isInstalled = true;
    return;
  }

  const REQUEST_KIND = 'request';
  const RESPONSE_KIND = 'response';
  const SOURCE = 'lumenExt';

  const pending = new Map();
  let nextId = 1;

  function call(action, payload) {
    return new Promise((resolve, reject) => {
      const id = nextId++;
      pending.set(id, { resolve, reject });
      window.postMessage(
        { source: SOURCE, kind: REQUEST_KIND, id, action, payload: payload || {} },
        '*'
      );
      setTimeout(() => {
        if (pending.has(id)) {
          pending.delete(id);
          reject(new Error('Lumen extension timed out (no response in 30s)'));
        }
      }, 30000);
    });
  }

  window.addEventListener('message', (event) => {
    if (event.source !== window) return;
    const d = event.data;
    if (!d || d.source !== SOURCE || d.kind !== RESPONSE_KIND) return;
    const p = pending.get(d.id);
    if (!p) return;
    pending.delete(d.id);
    if (d.error) p.reject(new Error(d.error));
    else p.resolve(d.result);
  });

  window.lumenExt = {
    version: '1.0',
    isInstalled: true,
    ping: () => call('ping'),
    searchInbox: (query) => call('searchInbox', { query }),
    readEmail: () => call('readEmail'),
    composeAndSend: (opts) => call('composeAndSend', opts),
    injectReply: (text) => call('injectReply', { text }),
    clickSend: () => call('clickSend'),
    selectAndRead: (index) => call('selectAndRead', { index }),
  };

  // Notify the app that the bridge is ready
  window.dispatchEvent(new CustomEvent('lumenExtReady', { detail: { version: '1.0' } }));
  console.log('[Lumen] page-bridge loaded — window.lumenExt is ready');
})();
