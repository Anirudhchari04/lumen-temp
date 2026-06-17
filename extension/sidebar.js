// sidebar.js — UI logic for the side panel
// Talks to Lumen backend (via fetch) and to content.js (via chrome.tabs.sendMessage)

const DEFAULT_BACKEND = 'https://lumen-demo.azurewebsites.net';

// ── State ────────────────────────────────────────────────────
let lumenToken = null;
let lumenUser = null;
let lastEmail = null;       // { subject, sender, senderEmail, body }
let lastReply = '';         // generated reply text
let lastCompose = '';       // generated compose text
let backendUrl = DEFAULT_BACKEND;

// ── DOM helpers ──────────────────────────────────────────────
const $ = (id) => document.getElementById(id);
function setStatus(el, msg, kind = 'info') {
  if (!msg) { el.innerHTML = ''; return; }
  el.innerHTML = `<div class="status ${kind}">${msg}</div>`;
}
function loadingHTML(label) {
  return `<span class="spinner"></span>${label || 'Working…'}`;
}

// ── Tabs ─────────────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(t => {
  t.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.section').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    $('tab-' + t.dataset.tab).classList.add('active');
  });
});

// ── Backend URL persistence ──────────────────────────────────
(async () => {
  const { backendUrl: stored } = await chrome.storage.local.get('backendUrl');
  if (stored) {
    backendUrl = stored;
    $('backendUrl').value = stored;
  } else {
    $('backendUrl').value = DEFAULT_BACKEND;
  }
})();

$('backendUrl').addEventListener('change', async (e) => {
  backendUrl = e.target.value.trim().replace(/\/$/, '');
  await chrome.storage.local.set({ backendUrl });
});

// ── Connection / auth ────────────────────────────────────────
async function refreshConnection() {
  const conn = $('connStatus');
  conn.textContent = 'checking…';
  conn.className = 'conn';

  const cached = await chrome.storage.local.get(['lumenToken', 'lumenUser']);
  if (cached.lumenToken) {
    lumenToken = cached.lumenToken;
    lumenUser = cached.lumenUser;
  }

  // Always try to refresh from open Lumen tabs
  try {
    const resp = await chrome.runtime.sendMessage({ action: 'getLumenToken' });
    if (resp?.token) {
      lumenToken = resp.token;
      lumenUser = resp.user;
    }
  } catch (e) {
    // Background may not be ready yet — fall through with cached value
  }

  if (lumenToken) {
    const name = lumenUser?.name || lumenUser?.email || 'connected';
    conn.textContent = '✓ ' + name;
    conn.className = 'conn ok';
  } else {
    conn.innerHTML = '<a href="#" id="signInLink" style="color:inherit;">sign in →</a>';
    conn.className = 'conn err';
    setTimeout(() => {
      const link = $('signInLink');
      if (link) {
        link.onclick = (e) => {
          e.preventDefault();
          chrome.runtime.sendMessage({ action: 'openLumenLogin', url: backendUrl + '/login' });
        };
      }
    }, 50);
  }
}
refreshConnection();
setInterval(refreshConnection, 30000); // re-check every 30s

// ── Backend fetch wrapper ────────────────────────────────────
async function lumenFetch(path, body) {
  if (!lumenToken) {
    await refreshConnection();
    if (!lumenToken) {
      throw new Error('Not signed in to Lumen. Open Lumen in another tab and sign in first.');
    }
  }
  const r = await fetch(backendUrl + path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer ' + lumenToken,
    },
    body: JSON.stringify(body),
  });
  if (r.status === 401) {
    lumenToken = null;
    await chrome.storage.local.remove(['lumenToken', 'lumenUser']);
    await refreshConnection();
    throw new Error('Session expired. Sign in to Lumen again.');
  }
  if (!r.ok) {
    const text = await r.text().catch(() => '');
    throw new Error(`Backend error ${r.status}: ${text.slice(0, 200)}`);
  }
  return r.json();
}

// ── Active tab helper ────────────────────────────────────────
async function activeOutlookTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) throw new Error('No active tab found.');
  if (!tab.url || !/outlook\.(office|live|cloud|office365)\.(com|microsoft)/.test(tab.url)) {
    throw new Error('Open this on outlook.office.com, outlook.live.com, or outlook.cloud.microsoft first.');
  }
  return tab;
}

async function tellContent(action, payload = {}) {
  const tab = await activeOutlookTab();
  return await chrome.tabs.sendMessage(tab.id, { action, ...payload });
}

// ── Read & Reply ─────────────────────────────────────────────
$('btnRead').addEventListener('click', async () => {
  const status = $('replyStatus');
  setStatus(status, loadingHTML('Reading email…'));
  try {
    const r = await tellContent('getEmailContent');
    if (!r?.ok) throw new Error(r?.error || 'Could not read email');
    lastEmail = r.data;
    if (!lastEmail.found) {
      setStatus(status, 'No email open. Click an email in Outlook first.', 'err');
      return;
    }
    $('emailMeta').style.display = 'block';
    $('emailMeta').innerHTML =
      `<strong>From:</strong> ${escapeHtml(lastEmail.sender)} ` +
      (lastEmail.senderEmail ? `&lt;${escapeHtml(lastEmail.senderEmail)}&gt;` : '') +
      `<br><strong>Subject:</strong> ${escapeHtml(lastEmail.subject)}`;
    $('emailBody').value = lastEmail.body;
    setStatus(status, '✓ Email loaded', 'ok');
  } catch (e) {
    setStatus(status, e.message, 'err');
  }
});

$('btnGenerateReply').addEventListener('click', async () => {
  const status = $('replyStatus');
  const instruction = $('replyInstruction').value.trim();
  if (!lastEmail) {
    setStatus(status, 'Read an email first.', 'err');
    return;
  }
  if (!instruction) {
    setStatus(status, 'Type an instruction for the reply.', 'err');
    return;
  }
  setStatus(status, loadingHTML('Asking Lumen…'));
  $('replyResult').textContent = '';
  try {
    const r = await lumenFetch('/lumen/comm/extension/reply', {
      subject: lastEmail.subject,
      sender: lastEmail.sender,
      sender_email: lastEmail.senderEmail,
      body: lastEmail.body,
      instruction,
    });
    lastReply = r.reply || '';
    $('replyResult').textContent = lastReply;
    setStatus(status, '✓ Reply generated', 'ok');
  } catch (e) {
    setStatus(status, e.message, 'err');
  }
});

$('btnCopyReply').addEventListener('click', async () => {
  if (!lastReply) return;
  await navigator.clipboard.writeText(lastReply);
  setStatus($('replyStatus'), '📋 Copied to clipboard', 'ok');
});

$('btnInjectReply').addEventListener('click', async () => {
  const status = $('replyStatus');
  if (!lastReply) { setStatus(status, 'Generate a reply first.', 'err'); return; }
  setStatus(status, loadingHTML('Injecting into Outlook…'));
  try {
    // Try to click Reply button so a compose pane is open
    await tellContent('clickReply');
    // Give Outlook a moment to render the compose pane
    await new Promise(r => setTimeout(r, 600));
    const r = await tellContent('injectReply', { text: lastReply });
    if (!r?.ok) throw new Error(r?.error || 'Could not find compose editor');
    // Log to Lumen outbox
    if (lastEmail?.senderEmail) {
      try {
        await lumenFetch('/lumen/comm/extension/log-sent', {
          to: lastEmail.senderEmail,
          subject: 'Re: ' + (lastEmail.subject || ''),
          body: lastReply,
        });
      } catch {}
    }
    setStatus(status, '✓ Injected — review & click Send in Outlook', 'ok');
  } catch (e) {
    setStatus(status, e.message, 'err');
  }
});

// ── Send Now (Reply) — inject + 3s cancel countdown + click Send ─────
let sendNowCancelled = false;
let sendNowTimer = null;

async function performSendNow({ to, subject, body, isReply, statusEl, btn }) {
  sendNowCancelled = false;
  const origLabel = btn.textContent;
  btn.disabled = false;

  try {
    if (isReply) {
      await tellContent('clickReply');
      await new Promise(r => setTimeout(r, 700));
    } else {
      await tellContent('openCompose');
      await new Promise(r => setTimeout(r, 800));
      if (to) await tellContent('setTo', { email: to });
      if (subject) await tellContent('setSubject', { subject });
      await new Promise(r => setTimeout(r, 250));
    }
    const ir = await tellContent('injectReply', { text: body });
    if (!ir?.ok) throw new Error(ir?.error || 'Could not inject into compose');

    // Countdown with cancel
    setStatus(statusEl, `Sending in <strong id="sendCountdown">3</strong>… <button id="cancelSend" style="margin-left:6px;padding:2px 8px;font-size:11px;">Cancel</button>`, 'info');
    const cancelBtn = document.getElementById('cancelSend');
    if (cancelBtn) cancelBtn.onclick = () => { sendNowCancelled = true; };

    for (let s = 3; s > 0; s--) {
      const el = document.getElementById('sendCountdown');
      if (el) el.textContent = s;
      await new Promise(r => setTimeout(r, 1000));
      if (sendNowCancelled) {
        setStatus(statusEl, 'Cancelled. Reply stays in the compose window — edit & send manually.', 'info');
        return;
      }
    }

    // Click Send
    const sendRes = await tellContent('clickSendButton');
    if (!sendRes?.ok) throw new Error(sendRes?.error || 'Could not find Send button in Outlook');

    // Log to outbox
    try {
      await lumenFetch('/lumen/comm/extension/log-sent', {
        to: to || lastEmail?.senderEmail || '',
        subject: subject || ('Re: ' + (lastEmail?.subject || '')),
        body,
      });
    } catch {}

    setStatus(statusEl, '✓ Sent!', 'ok');
  } catch (e) {
    setStatus(statusEl, e.message, 'err');
  } finally {
    btn.disabled = false;
    btn.textContent = origLabel;
  }
}

$('btnSendNow').addEventListener('click', async () => {
  const status = $('replyStatus');
  if (!lastReply) { setStatus(status, 'Generate a reply first.', 'err'); return; }
  await performSendNow({
    to: lastEmail?.senderEmail || '',
    subject: 'Re: ' + (lastEmail?.subject || ''),
    body: lastReply,
    isReply: true,
    statusEl: status,
    btn: $('btnSendNow'),
  });
});

// ── Compose ──────────────────────────────────────────────────
$('btnGenerateCompose').addEventListener('click', async () => {
  const status = $('composeStatus');
  const to = $('composeTo').value.trim();
  const subject = $('composeSubject').value.trim();
  const instruction = $('composeInstruction').value.trim();
  if (!instruction) {
    setStatus(status, 'Describe what the email should say.', 'err');
    return;
  }
  setStatus(status, loadingHTML('Asking Lumen…'));
  $('composeResult').textContent = '';
  try {
    const r = await lumenFetch('/lumen/comm/extension/compose', {
      to, subject, instruction,
    });
    lastCompose = r.email_body || '';
    $('composeResult').textContent = lastCompose;
    setStatus(status, '✓ Email generated', 'ok');
  } catch (e) {
    setStatus(status, e.message, 'err');
  }
});

$('btnCopyCompose').addEventListener('click', async () => {
  if (!lastCompose) return;
  await navigator.clipboard.writeText(lastCompose);
  setStatus($('composeStatus'), '📋 Copied to clipboard', 'ok');
});

$('btnSendNowCompose').addEventListener('click', async () => {
  const status = $('composeStatus');
  if (!lastCompose) { setStatus(status, 'Generate an email first.', 'err'); return; }
  const to = $('composeTo').value.trim();
  if (!to) { setStatus(status, 'Add a recipient email first.', 'err'); return; }
  const subject = $('composeSubject').value.trim();
  await performSendNow({
    to, subject, body: lastCompose,
    isReply: false,
    statusEl: status,
    btn: $('btnSendNowCompose'),
  });
});

$('btnOpenFill').addEventListener('click', async () => {
  const status = $('composeStatus');
  if (!lastCompose) { setStatus(status, 'Generate an email first.', 'err'); return; }
  const to = $('composeTo').value.trim();
  const subject = $('composeSubject').value.trim();
  setStatus(status, loadingHTML('Opening Outlook compose…'));
  try {
    await tellContent('openCompose');
    await new Promise(r => setTimeout(r, 800));
    if (to) await tellContent('setTo', { email: to });
    if (subject) await tellContent('setSubject', { subject });
    await new Promise(r => setTimeout(r, 200));
    const r = await tellContent('injectReply', { text: lastCompose });
    if (!r?.ok) throw new Error(r?.error || 'Could not fill compose editor');
    // Log to outbox
    if (to) {
      try {
        await lumenFetch('/lumen/comm/extension/log-sent', {
          to, subject, body: lastCompose,
        });
      } catch {}
    }
    setStatus(status, '✓ Compose filled — review & click Send', 'ok');
  } catch (e) {
    setStatus(status, e.message, 'err');
  }
});

// ── Util ─────────────────────────────────────────────────────
function escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}
