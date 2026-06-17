// background.js — service worker
// Responsibilities:
//   1. Open the side panel when the extension icon is clicked
//   2. Auto-read the Lumen JWT from any open lumen-demo tab

const LUMEN_ORIGINS = [
  'https://lumen-demo.azurewebsites.net',
  'http://localhost:8000',
];

// 1. Open side panel on action click
chrome.action.onClicked.addListener(async (tab) => {
  try {
    await chrome.sidePanel.open({ tabId: tab.id });
  } catch (e) {
    console.error('Failed to open side panel:', e);
  }
});

// ── Outlook tab routing ──────────────────────────────────────
const OUTLOOK_PATTERNS = [
  'https://outlook.office.com/*',
  'https://outlook.office365.com/*',
  'https://outlook.live.com/*',
  'https://outlook.cloud.microsoft/*',
];

async function findOutlookTab() {
  const tabs = await chrome.tabs.query({ url: OUTLOOK_PATTERNS });
  if (tabs.length === 0) return null;
  const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (activeTab && OUTLOOK_PATTERNS.some(p => {
    const host = p.replace('https://', '').replace('/*', '');
    return activeTab.url?.startsWith('https://' + host);
  })) {
    return activeTab;
  }
  return tabs.sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0))[0];
}

async function sendToOutlookEnsuring(action, payload = {}) {
  const tab = await findOutlookTab();
  if (!tab) {
    return { ok: false, error: 'No Outlook tab open. Open outlook.office.com first.' };
  }
  async function trySend() {
    return await chrome.tabs.sendMessage(tab.id, { action, ...payload });
  }
  try {
    return await trySend();
  } catch (e) {
    if (!/Receiving end does not exist|Could not establish connection/i.test(String(e?.message || e))) {
      throw e;
    }
    try {
      await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ['content.js'] });
      await new Promise(r => setTimeout(r, 250));
      return await trySend();
    } catch (e2) {
      return { ok: false, error: 'Could not inject Lumen into Outlook. Refresh the Outlook tab and try again.' };
    }
  }
}

// 2. Allow side panel + lumen-bridge to ask for the JWT or route to Outlook
const OUTLOOK_ACTIONS = new Set([
  'getEmailContent', 'openCompose', 'clickReply', 'setTo', 'setSubject',
  'injectReply', 'clickSendButton', 'searchInbox', 'listSearchResults',
  'selectInboxRow', 'ping',
]);

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === 'getLumenToken') {
    findLumenTokenFromTabs()
      .then(result => sendResponse(result))
      .catch(err => sendResponse({ token: null, error: String(err) }));
    return true;
  }
  if (msg.action === 'openLumenLogin') {
    chrome.tabs.create({ url: msg.url || 'https://lumen-demo.azurewebsites.net/login' });
    sendResponse({ opened: true });
    return false;
  }
  if (OUTLOOK_ACTIONS.has(msg.action)) {
    sendToOutlookEnsuring(msg.action, msg)
      .then(result => sendResponse(result))
      .catch(err => sendResponse({ ok: false, error: String(err) }));
    return true;
  }
});

async function findLumenTokenFromTabs() {
  // Find any open tab on a Lumen origin
  const allTabs = await chrome.tabs.query({});
  const lumenTabs = allTabs.filter(t =>
    t.url && LUMEN_ORIGINS.some(origin => t.url.startsWith(origin))
  );

  if (lumenTabs.length === 0) {
    return { token: null, error: 'No Lumen tab open. Open and sign in to Lumen first.' };
  }

  // Try each Lumen tab until we find a token
  for (const tab of lumenTabs) {
    try {
      const [{ result }] = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => {
          const token = localStorage.getItem('lumen.token');
          const userRaw = localStorage.getItem('lumen.user');
          let user = null;
          try { user = userRaw ? JSON.parse(userRaw) : null; } catch {}
          return { token, user };
        },
      });
      if (result?.token) {
        // Persist for sidebar to read
        await chrome.storage.local.set({ lumenToken: result.token, lumenUser: result.user });
        return { token: result.token, user: result.user };
      }
    } catch (e) {
      console.warn('Could not read JWT from tab', tab.id, e);
    }
  }

  return { token: null, error: 'Found Lumen tabs but no JWT — sign in first.' };
}
