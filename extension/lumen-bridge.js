// lumen-bridge.js — content script on lumen-demo.azurewebsites.net
// Lives in the extension's isolated world. Its jobs:
//   1. Inject page-bridge.js into the PAGE's main world (so the React app can use window.lumenExt)
//   2. Listen for postMessage requests from the page-world bridge and forward them to background.js

(function () {
  'use strict';

  // ── 1. Inject page-bridge.js into the page's main world ────
  // Using script.src with chrome.runtime.getURL() bypasses page CSP because the
  // resource lives at a chrome-extension:// URL, not as inline JS.
  // Requires "web_accessible_resources" in manifest.json.
  try {
    const script = document.createElement('script');
    script.src = chrome.runtime.getURL('page-bridge.js');
    script.async = false;
    script.onload = () => script.remove();
    (document.head || document.documentElement).appendChild(script);
  } catch (e) {
    console.error('[Lumen bridge] Failed to inject page-bridge:', e);
  }

  // ── 2. Handle messages from the page world ─────────────────
  window.addEventListener('message', async (event) => {
    if (event.source !== window) return;
    const d = event.data;
    if (!d || d.source !== 'lumenExt' || d.kind !== 'request') return;

    const respond = (result, error) => {
      window.postMessage(
        { source: 'lumenExt', kind: 'response', id: d.id, result, error },
        '*'
      );
    };

    try {
      const result = await handleRequest(d.action, d.payload || {});
      respond(result);
    } catch (e) {
      respond(null, String(e?.message || e));
    }
  });

  async function handleRequest(action, payload) {
    switch (action) {
      case 'ping':
        return { ok: true, version: '1.0' };
      case 'searchInbox':
        return await sendToBackground('searchInbox', { query: payload.query });
      case 'readEmail':
        return await sendToBackground('getEmailContent');
      case 'injectReply':
        return await sendToBackground('injectReply', { text: payload.text });
      case 'clickSend':
        return await sendToBackground('clickSendButton');
      case 'selectAndRead': {
        await sendToBackground('selectInboxRow', { index: payload.index });
        await new Promise(r => setTimeout(r, 800));
        return await sendToBackground('getEmailContent');
      }
      case 'composeAndSend': {
        await sendToBackground('openCompose');
        // outlook.cloud.microsoft is slower to render the compose pane
        await new Promise(r => setTimeout(r, 1800));
        // Set To FIRST and wait for chip resolution before subject/body
        let toOk = true;
        if (payload.to) {
          const rTo = await sendToBackground('setTo', { email: payload.to });
          toOk = !!rTo?.ok;
          // Extra wait so Outlook can resolve "user@domain" → person chip
          await new Promise(r => setTimeout(r, 800));
        }
        if (payload.subject) await sendToBackground('setSubject', { subject: payload.subject });
        await new Promise(r => setTimeout(r, 400));
        await sendToBackground('injectReply', { text: payload.body || '' });
        await new Promise(r => setTimeout(r, 600));
        if (payload.send) {
          // Refuse to click Send if the recipient was requested but couldn't be set —
          // otherwise we'd report a phantom success while Outlook silently rejects.
          if (payload.to && !toOk) {
            return { ok: false, error: 'Could not populate the To field in Outlook. Send the email manually from the compose window.' };
          }
          // Try click Send up to 3 times — the button may render slightly after inject
          for (let attempt = 0; attempt < 3; attempt++) {
            const r = await sendToBackground('clickSendButton');
            if (r?.ok) return { ok: true, sent: true };
            await new Promise(res => setTimeout(res, 500));
          }
          return { ok: false, error: 'Could not find Send button in Outlook compose pane' };
        }
        return { ok: true, sent: false, toOk };
      }
      default:
        throw new Error('Unknown action: ' + action);
    }
  }

  function sendToBackground(action, extra = {}) {
    return new Promise((resolve, reject) => {
      try {
        chrome.runtime.sendMessage({ action, ...extra }, (resp) => {
          if (chrome.runtime.lastError) {
            reject(new Error(chrome.runtime.lastError.message));
          } else {
            resolve(resp);
          }
        });
      } catch (e) {
        reject(e);
      }
    });
  }

  console.log('[Lumen bridge] content script active on', location.href);
})();
