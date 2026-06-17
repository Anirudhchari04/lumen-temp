// content.js — runs on outlook.office.com pages
// Reads the currently-open email and injects text into the compose window.
//
// Outlook Web uses heavily-obfuscated class names — we rely on aria-label,
// role, and data-testid attributes which Microsoft tends to keep stable.

(function () {
  'use strict';

  // ── Helpers ────────────────────────────────────────────────

  function querySelectorWithFallbacks(selectors, root = document) {
    for (const sel of selectors) {
      const el = root.querySelector(sel);
      if (el) return el;
    }
    return null;
  }

  function clean(text) {
    return (text || '').replace(/\s+\n/g, '\n').replace(/\n\s+/g, '\n').trim();
  }

  // ── Reading the current email ──────────────────────────────

  function readCurrentEmail() {
    const subjectEl = querySelectorWithFallbacks([
      '[data-testid="ConversationTopic"]',
      '[data-testid*="onversationTopic"]',
      '[data-testid*="ubjectLine"]',
      'div[role="heading"][aria-level="1"]',
      'h1[role="heading"]',
      'span[role="heading"][aria-level="2"]',
      // outlook.cloud.microsoft variant
      'div[id*="Subject"] span',
    ]);
    const subject = clean(subjectEl?.textContent || '');

    // Sender: try a few patterns
    let sender = '';
    let senderEmail = '';
    const senderBtn = querySelectorWithFallbacks([
      '[data-testid="message-header-from"] [role="button"]',
      '[data-testid*="ender"] [role="button"]',
      'div[role="region"][aria-label*="essage header"] span[title*="@"]',
      'span[role="button"][title*="@"]',
      'button[title*="@"]',
      'span[title*="@"]',
    ]);
    if (senderBtn) {
      sender = clean(senderBtn.textContent);
      const title = senderBtn.getAttribute('title') || senderBtn.getAttribute('aria-label') || '';
      const emailMatch = title.match(/[\w.+-]+@[\w-]+\.[\w.-]+/);
      senderEmail = emailMatch ? emailMatch[0] : '';
    }
    // Last resort: extract any email address from the message header area
    if (!senderEmail) {
      const headerArea = querySelectorWithFallbacks([
        '[data-testid*="message-header"]',
        'div[role="region"][aria-label*="essage header" i]',
        'div[role="heading"]',
      ]);
      if (headerArea) {
        const m = (headerArea.outerHTML || '').match(/[\w.+-]+@[\w-]+\.[\w.-]+/);
        if (m) senderEmail = m[0];
      }
    }

    // Body: try a LOT of selectors. outlook.cloud.microsoft uses different DOM.
    const bodySelectors = [
      '[data-testid="emailBodyContent"]',
      '[data-testid*="essageBody"]',
      '[data-testid*="MessageBody"]',
      'div[role="region"][aria-label*="essage body"]',
      'div[role="region"][aria-label*="Message body"]',
      'div[class*="ReadingPane"] div[role="document"]',
      'div[role="document"]',
      // outlook.cloud.microsoft fallbacks
      '[data-app-section="MailReadingPane"] [role="document"]',
      '[data-app-section="MailReadingPane"] div[dir]',
      '[data-app-section*="Reading"] div[dir]',
      'div[id*="UniqueMessageBody"]',
      // iframe-based body (some Outlook variants embed body in iframe)
      'iframe[id*="MessageBody"]',
      'iframe[title*="essage body" i]',
    ];
    let body = '';
    for (const sel of bodySelectors) {
      const el = document.querySelector(sel);
      if (!el) continue;
      // If it's an iframe, try reading its document
      if (el.tagName === 'IFRAME') {
        try {
          const doc = el.contentDocument || el.contentWindow?.document;
          if (doc) {
            body = clean(doc.body?.innerText || doc.body?.textContent || '');
            if (body.length > 20) break;
          }
        } catch {}
        continue;
      }
      body = clean(el.innerText || el.textContent || '');
      if (body.length > 20) break;
    }

    // Last-resort body fallback: largest <div> with substantive text in the reading pane area
    if (!body || body.length < 20) {
      const readingPane = querySelectorWithFallbacks([
        '[data-app-section="MailReadingPane"]',
        '[data-app-section*="Reading"]',
        'div[role="main"]',
        'div[class*="ReadingPane"]',
      ]);
      if (readingPane) {
        const candidates = readingPane.querySelectorAll('div, p, section');
        let largest = null;
        let largestLen = 100;  // require at least 100 chars
        for (const c of candidates) {
          if (c.offsetParent === null) continue;
          const t = (c.innerText || '').trim();
          if (t.length > largestLen && t.length < 50000) {
            // Exclude UI text — must contain a sentence
            if (/[a-z]\s+[a-z]/.test(t.slice(0, 200))) {
              largest = c;
              largestLen = t.length;
            }
          }
        }
        if (largest) body = clean(largest.innerText);
      }
    }

    if (!body) {
      console.log('[Lumen] readCurrentEmail — body not found. Sample reading pane HTML:');
      const rp = document.querySelector('[data-app-section*="Reading"]') || document.querySelector('[role="main"]');
      if (rp) console.log(rp.outerHTML.slice(0, 1500));
    }

    return {
      subject,
      sender,
      senderEmail,
      body,
      found: !!(subject || body),
    };
  }

  // ── Compose: open, set fields, inject body ─────────────────

  function openNewCompose() {
    const newBtn = querySelectorWithFallbacks([
      'button[aria-label="New mail"]',
      'button[aria-label="New message"]',
      'button[aria-label*="New mail"]',
      'button[aria-label*="New message" i]',
      '[data-testid="newMailButton"]',
      '[data-testid*="NewMail"]',
      'div[role="button"][aria-label="New mail"]',
      'div[role="button"][aria-label*="New mail" i]',
      // outlook.cloud.microsoft variant — uses "Compose" or "+ New"
      'button[aria-label="Compose"]',
      'button[aria-label*="Compose" i]',
    ]);
    if (newBtn) {
      newBtn.click();
      return true;
    }
    // Fallback: keyboard shortcut N (Outlook default for "new mail")
    try {
      document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'n', code: 'KeyN', keyCode: 78, which: 78, bubbles: true }));
      return true;
    } catch {
      return false;
    }
  }

  function _reactSetInputValue(input, value) {
    // Outlook's compose uses React-controlled inputs; plain `.value =` is ignored.
    // Use the native setter so React's synthetic onChange picks it up.
    try {
      const proto = input.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
      setter.call(input, value);
    } catch {
      input.value = value;
    }
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function _isVisible(el) {
    if (!el) return false;
    if (el.offsetParent === null) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  function _looksLikeToField(el) {
    if (!el || !_isVisible(el)) return false;
    const aria = (el.getAttribute('aria-label') || '').toLowerCase();
    const ph = (el.getAttribute('placeholder') || '').toLowerCase();
    const auto = (el.getAttribute('autocomplete') || '').toLowerCase();
    // Hard exclusions
    if (/(search|subject|body|message|cc|bcc|find|filter)/i.test(aria + ' ' + ph)) return false;
    // Positive signals
    if (/(^|\b)to(\b|:)/i.test(aria)) return true;
    if (/recipient/i.test(aria + ' ' + ph)) return true;
    if (/^to /i.test(ph)) return true;
    if (auto === 'email' || auto === 'username') return true;
    return false;
  }

  function _findToFieldAggressive() {
    // Strategy 0: tab order — after openNewCompose, Outlook auto-focuses the To field.
    // If the active element is a text-entry inside the compose pane, use it.
    const active = document.activeElement;
    if (active && active !== document.body) {
      const tag = active.tagName.toLowerCase();
      const isTextInput = (
        (tag === 'input' && /^(text|email|search|tel|url|)$/i.test(active.type || '')) ||
        active.getAttribute('contenteditable') === 'true' ||
        active.getAttribute('role') === 'textbox' ||
        active.getAttribute('role') === 'combobox'
      );
      if (isTextInput && _isVisible(active)) {
        const aria = (active.getAttribute('aria-label') || '').toLowerCase();
        // Reject if it's clearly NOT a recipient field
        if (!/search|subject|body|cc|bcc|filter/i.test(aria)) {
          return active;
        }
      }
    }

    // Strategy 1: exact aria-label match
    const exactSelectors = [
      'input[aria-label="To"]',
      'input[aria-label="To recipients"]',
      'input[aria-label="To Recipients"]',
      'input[aria-label="To recipient"]',
      'input[aria-label*="o recipient" i]',
      '[data-testid="OnyxAddressInput"] input',
      '[data-testid*="AddressInput"] input',
      'div[role="textbox"][aria-label="To"]',
      'div[contenteditable="true"][aria-label="To"]',
      'div[role="combobox"][aria-label="To"]',
      'div[role="combobox"][aria-label*="recipient" i]',
    ];
    let el = querySelectorWithFallbacks(exactSelectors);
    if (el && _isVisible(el)) return el;

    // Strategy 2: aria-label *starts* with "To" — leaf input/contenteditable only
    const candidates = document.querySelectorAll(
      'input[aria-label], div[role="textbox"][aria-label], div[contenteditable="true"][aria-label], [role="combobox"][aria-label]'
    );
    for (const c of candidates) {
      if (_looksLikeToField(c)) return c;
    }

    // Strategy 3: find the visible "To" label/heading, then the nearest descendant/sibling input
    const labels = document.querySelectorAll('label, span, div');
    for (const lbl of labels) {
      const txt = (lbl.textContent || '').trim();
      if (txt !== 'To' && txt !== 'To:' && txt !== 'To...') continue;
      if (!_isVisible(lbl)) continue;
      let parent = lbl.parentElement;
      for (let depth = 0; depth < 5 && parent; depth++) {
        const inp = parent.querySelector(
          'input:not([type="hidden"]):not([type="button"]):not([type="submit"]):not([type="checkbox"]), ' +
          'div[contenteditable="true"], div[role="textbox"], [role="combobox"]'
        );
        if (inp && _isVisible(inp) && !_looksLikeToField(inp) === false) {
          // accept if it doesn't look like search/subject/body
          const aria = (inp.getAttribute('aria-label') || '').toLowerCase();
          if (!/search|subject|body|cc|bcc|filter/i.test(aria)) return inp;
        }
        parent = parent.parentElement;
      }
    }

    // Strategy 4: anchor on Send button, find first text-entry within the compose pane
    const sendBtn = document.querySelector(
      'button[aria-label="Send"], [data-testid="ComposeSendButton"], button[title="Send"]'
    );
    if (sendBtn) {
      let container = sendBtn.closest('[role="dialog"], [role="region"], form, [data-app-section]') || sendBtn.parentElement;
      for (let depth = 0; depth < 8 && container; depth++) {
        const inputs = container.querySelectorAll(
          'input:not([type="hidden"]):not([type="button"]):not([type="submit"]):not([type="checkbox"]), ' +
          'div[contenteditable="true"], [role="textbox"], [role="combobox"]'
        );
        for (const inp of inputs) {
          if (!_isVisible(inp)) continue;
          const aria = (inp.getAttribute('aria-label') || '').toLowerCase();
          const ph = (inp.getAttribute('placeholder') || '').toLowerCase();
          // Exclude search/subject/body/cc/bcc
          if (/search|subject|body|cc|bcc|filter|message/i.test(aria + ' ' + ph)) continue;
          // The To field is typically the first input in the compose container
          return inp;
        }
        container = container.parentElement;
      }
    }

    // Strategy 5: last resort — click the visible "To" pill/label, then re-check activeElement
    const toPill = Array.from(document.querySelectorAll('button, div[role="button"], span'))
      .find(el => {
        if (!_isVisible(el)) return false;
        const txt = (el.textContent || '').trim();
        return txt === 'To' || txt === '+ To' || txt === 'To...';
      });
    if (toPill) {
      try { toPill.click(); } catch {}
      // After click, activeElement should be the To input
      const next = document.activeElement;
      if (next && next !== document.body && _isVisible(next)) return next;
    }

    return null;
  }

  function _dumpComposeStructure() {
    // Helper that surfaces the compose pane DOM so the user can share it for debugging.
    const sendBtn = document.querySelector('button[aria-label="Send"], [data-testid="ComposeSendButton"], button[title="Send"]');
    if (!sendBtn) {
      console.log('[Lumen DEBUG] No Send button found — compose pane may not be open');
      return;
    }
    let pane = sendBtn.closest('[role="dialog"], [role="region"], form') || sendBtn.parentElement;
    for (let i = 0; i < 5 && pane && pane.tagName !== 'BODY'; i++) {
      pane = pane.parentElement;
    }
    console.log('[Lumen DEBUG] === COMPOSE PANE STRUCTURE ===');
    const inputs = pane?.querySelectorAll('input, [contenteditable], [role="textbox"], [role="combobox"]') || [];
    inputs.forEach((el, idx) => {
      if (el.offsetParent === null) return;
      const aria = el.getAttribute('aria-label') || '';
      const ph = el.getAttribute('placeholder') || '';
      const role = el.getAttribute('role') || '';
      const tag = el.tagName;
      const type = el.getAttribute('type') || '';
      console.log(`[Lumen DEBUG]   #${idx} ${tag}[type="${type}"][role="${role}"] aria="${aria}" placeholder="${ph}"`);
    });
    console.log('[Lumen DEBUG] === END ===');
  }

  async function _typeIntoField(field, text) {
    // Multi-strategy text insertion. Returns true if any strategy seemed to work.
    field.focus();
    await new Promise(r => setTimeout(r, 50));

    const tag = field.tagName.toLowerCase();
    const isEditable = field.getAttribute('contenteditable') === 'true' || field.getAttribute('role') === 'textbox';
    let success = false;

    // Strategy A: React-friendly value setter for input/textarea
    if (tag === 'input' || tag === 'textarea') {
      try {
        _reactSetInputValue(field, text);
        success = true;
      } catch {}
    }

    // Strategy B: contenteditable insertText
    if (!success && isEditable) {
      try {
        const sel = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(field);
        sel.removeAllRanges();
        sel.addRange(range);
        document.execCommand('delete', false);
        document.execCommand('insertText', false, text);
        field.dispatchEvent(new Event('input', { bubbles: true }));
        success = true;
      } catch {}
    }

    // Strategy C: simulated paste event (works on many React-controlled inputs)
    if (!success) {
      try {
        const dt = new DataTransfer();
        dt.setData('text/plain', text);
        const pasteEvent = new ClipboardEvent('paste', {
          clipboardData: dt, bubbles: true, cancelable: true,
        });
        field.dispatchEvent(pasteEvent);
        // Also try setting value as fallback
        if (tag === 'input' || tag === 'textarea') {
          _reactSetInputValue(field, text);
        } else {
          field.textContent = text;
          field.dispatchEvent(new Event('input', { bubbles: true }));
        }
        success = true;
      } catch {}
    }

    return success;
  }

  async function setComposeTo(email) {
    if (!email) return false;

    // Wait a beat for the compose pane to finish rendering (outlook.cloud.microsoft is slow).
    let toField = null;
    for (let attempt = 0; attempt < 8; attempt++) {
      toField = _findToFieldAggressive();
      if (toField) break;
      await new Promise(r => setTimeout(r, 400));
    }

    if (!toField) {
      console.error('[Lumen] setComposeTo: could not find the To field after 8 attempts (3.2s).');
      _dumpComposeStructure();
      return false;
    }

    console.log('[Lumen] setComposeTo: found field —',
      toField.tagName,
      'aria=' + (toField.getAttribute('aria-label') || ''),
      'role=' + (toField.getAttribute('role') || '')
    );

    const typed = await _typeIntoField(toField, email);
    if (!typed) {
      console.error('[Lumen] setComposeTo: could not insert text into the To field');
      return false;
    }

    // Outlook converts "user@domain" → chip when you press Enter or Tab.
    await new Promise(r => setTimeout(r, 300));
    ['keydown', 'keypress', 'keyup'].forEach(eventType => {
      toField.dispatchEvent(new KeyboardEvent(eventType, {
        key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true,
      }));
    });
    await new Promise(r => setTimeout(r, 200));
    toField.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', code: 'Tab', keyCode: 9, which: 9, bubbles: true }));

    // Verify a chip was created (or at least the text persisted). If neither, return false.
    await new Promise(r => setTimeout(r, 400));
    const pane = toField.closest('[role="dialog"], [role="region"], form') || document.body;
    const chip = pane.querySelector(`[title*="${email}" i], [aria-label*="${email}" i], [data-testid*="recipient" i]`);
    const stillHasText = (toField.value || toField.textContent || '').toLowerCase().includes(email.toLowerCase().split('@')[0]);
    if (!chip && !stillHasText) {
      console.warn('[Lumen] setComposeTo: typing seemed to succeed but no chip or persisted text found');
      return false;
    }
    return true;
  }

  function setComposeSubject(subject) {
    if (!subject) return false;
    const subInput = querySelectorWithFallbacks([
      'input[aria-label="Add a subject"]',
      'input[aria-label*="Subject"]',
      '[data-testid="ComposeSubject"]',
      'input[placeholder*="Subject"]',
    ]);
    if (subInput) {
      subInput.focus();
      _reactSetInputValue(subInput, subject);
      return true;
    }
    // Newer Outlook may use contenteditable for subject too
    const subEditable = querySelectorWithFallbacks([
      'div[role="textbox"][aria-label*="Subject"]',
      'div[contenteditable="true"][aria-label*="Subject"]',
    ]);
    if (subEditable) {
      subEditable.focus();
      try { document.execCommand('insertText', false, subject); }
      catch { subEditable.textContent = subject; }
      return true;
    }
    return false;
  }

  function injectReply(text) {
    if (!text) return false;
    const editor = querySelectorWithFallbacks([
      'div[contenteditable="true"][aria-label*="essage body"]',
      'div[contenteditable="true"][aria-label*="Message body"]',
      'div[role="textbox"][contenteditable="true"]',
      'div[contenteditable="true"][aria-label*="Compose"]',
    ]);
    if (!editor) return false;
    editor.focus();
    // Clear existing content
    try {
      const sel = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(editor);
      sel.removeAllRanges();
      sel.addRange(range);
    } catch (e) {}
    // Insert as plain text so React picks it up
    try {
      document.execCommand('insertText', false, text);
    } catch (e) {
      // Fallback
      editor.innerHTML = text.replace(/\n/g, '<br>');
      editor.dispatchEvent(new Event('input', { bubbles: true }));
    }
    return true;
  }

  // ── Send button (one-click send from extension/Lumen) ─────

  async function clickSendButton() {
    // Try strict selectors first (least likely to match wrong button)
    let sendBtn = querySelectorWithFallbacks([
      'button[aria-label="Send"]',
      'button[aria-label="Send"][role="button"]',
      '[data-testid="ComposeSendButton"]',
      '[data-testid*="SendButton"]',
      'button[title="Send"]',
      'div[role="button"][aria-label="Send"]',
      // outlook.cloud.microsoft variants
      'button[aria-label*="Send" i]:not([aria-label*="later" i]):not([aria-label*="schedule" i])',
      'div[role="button"][aria-label*="Send" i]:not([aria-label*="later" i]):not([aria-label*="schedule" i])',
    ]);
    // Last resort: scan ALL buttons in the compose pane area for visible "Send" text
    if (!sendBtn) {
      const allButtons = document.querySelectorAll('button, div[role="button"]');
      for (const btn of allButtons) {
        const label = (btn.getAttribute('aria-label') || btn.textContent || '').trim();
        // Match exactly "Send" — ignore "Send later", "Schedule send", etc.
        if (/^Send$/i.test(label) && btn.offsetParent !== null) {
          sendBtn = btn;
          break;
        }
      }
    }
    if (!sendBtn) return false;

    sendBtn.click();

    // Wait briefly and verify the send actually completed — Outlook closes the compose
    // pane (and the Send button) once the message goes out. If the Send button is still
    // visible after 1.5s, Outlook silently rejected (usually due to missing recipient).
    await new Promise(r => setTimeout(r, 1500));
    const stillVisible = document.contains(sendBtn) && sendBtn.offsetParent !== null;
    if (stillVisible) {
      return false;  // Send did NOT fire — caller should report failure
    }
    return true;
  }

  // ── Outlook search (uses Outlook's own search box) ─────────

  function findSearchInput() {
    return querySelectorWithFallbacks([
      'input[aria-label="Search"]',
      'input[type="search"]',
      '[role="searchbox"] input',
      'input[placeholder*="Search"]',
    ]);
  }

  function _clearSearchBox() {
    const input = findSearchInput();
    if (!input) return false;
    input.focus();
    try {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
      setter.call(input, '');
    } catch {
      input.value = '';
    }
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    // Press Escape to dismiss the search view, then click somewhere neutral
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true }));
    // Try clicking the Inbox folder to force back to inbox view
    const inboxLink = querySelectorWithFallbacks([
      'div[role="treeitem"][aria-label*="Inbox" i]',
      'div[role="treeitem"] span[title="Inbox"]',
      'a[aria-label*="Inbox" i]',
      'button[aria-label*="Inbox" i]',
    ]);
    if (inboxLink) {
      try { inboxLink.click(); } catch {}
    }
    return true;
  }

  async function searchInbox(query) {
    // Empty query → just scan visible inbox (used by "show my recent emails")
    if (!query || !query.trim()) {
      const scan = listSearchResults();
      if (!scan.results.length) {
        return { ok: false, error: 'Could not find any email rows in Outlook. Try refreshing the Outlook tab.' };
      }
      return { ok: true, source: 'inbox-scan', results: scan.results.slice(0, 10) };
    }

    const queryLower = query.toLowerCase().replace(/^from:/, '').trim();
    const tokens = queryLower.split(/[\s/,]+/).filter(t => t.length >= 2);

    // STRATEGY 1: try Outlook's own search box (faster + scoped to mailbox)
    const input = findSearchInput();
    if (input) {
      input.focus();
      try {
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        setter.call(input, query);
      } catch {
        input.value = query;
      }
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      ['keydown', 'keypress', 'keyup'].forEach(eventType => {
        input.dispatchEvent(new KeyboardEvent(eventType, {
          key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true,
        }));
      });
      await new Promise(r => setTimeout(r, 2500));
      let result = listSearchResults();
      if (!result.results.length) {
        await new Promise(r => setTimeout(r, 1500));
        result = listSearchResults();
      }
      if (result.results.length) {
        return { ok: true, source: 'outlook-search', ...result };
      }
    }

    // STRATEGY 2: search returned nothing OR couldn't find search box —
    // CLEAR the search box (to make the inbox visible again) then scan + local filter.
    console.log('[Lumen] Outlook search returned no results — clearing search and falling back to inbox scan');
    _clearSearchBox();
    await new Promise(r => setTimeout(r, 1200));  // wait for inbox to re-render

    const scan = listSearchResults();
    if (!scan.results.length) {
      return { ok: false, error: 'Could not find any email rows in Outlook. Try refreshing the Outlook tab.' };
    }

    // Fuzzy match — at least ONE token in subject/sender/email/snippet
    const matchesQuery = (row) => {
      const haystack = (
        (row.subject || '') + ' ' +
        (row.sender || '') + ' ' +
        (row.senderEmail || '') + ' ' +
        (row.snippet || '')
      ).toLowerCase();
      return tokens.some(t => haystack.includes(t));
    };

    const filtered = scan.results.filter(matchesQuery);
    if (filtered.length) {
      return {
        ok: true,
        source: 'inbox-scan',
        results: filtered,
        note: `Filtered from ${scan.results.length} recent emails`,
      };
    }
    // No matches — return all visible so the user has SOMETHING to work with
    return {
      ok: true,
      source: 'inbox-scan',
      results: scan.results.slice(0, 10),
      note: `No emails matched "${queryLower}" in your recent inbox — showing the latest ${Math.min(scan.results.length, 10)} so you can browse.`,
    };
  }

  // Public method: scan inbox without searching (just lists what's currently visible)
  function scanInbox() {
    return listSearchResults();
  }

  function listSearchResults() {
    // Outlook DOM varies a LOT across outlook.office.com / outlook.cloud.microsoft / etc.
    // Try a wide net of selectors, then fall back to heuristics.

    const rowSelectors = [
      // Classic Outlook Web
      'div[role="listbox"] div[role="option"]',
      'div[role="list"] div[role="listitem"]',
      'div[role="option"][aria-label]',
      'div[role="listitem"][aria-label]',
      'div[role="row"][aria-label]',
      // outlook.cloud.microsoft new UI
      '[data-convid]',
      '[data-item-key]',
      '[data-list-id] [role="option"]',
      '[data-list-id] [role="listitem"]',
      '[data-list-id] > div',
      // Catch-all role-based
      '[role="option"]',
      '[role="listitem"]',
    ];
    let rows = [];
    let matchedSel = null;
    for (const sel of rowSelectors) {
      const found = document.querySelectorAll(sel);
      // Filter to only elements that have non-trivial content (skip empty wrappers)
      const filtered = Array.from(found).filter(el => {
        const text = (el.textContent || '').trim();
        return text.length > 10 && text.length < 2000 && el.offsetParent !== null;
      });
      if (filtered.length >= 1) {
        rows = filtered;
        matchedSel = sel;
        break;
      }
    }

    // Fallback 1: find aria-label elements that look like email rows
    if (!rows.length) {
      const candidates = document.querySelectorAll('[aria-label]');
      const looksLikeRow = [];
      for (const el of candidates) {
        const label = el.getAttribute('aria-label') || '';
        if (label.length > 20 && label.length < 500 &&
            (label.includes('@') || /\d{1,2}\/\d{1,2}|AM|PM|yesterday|today/i.test(label)) &&
            el.children.length >= 1 && el.offsetParent !== null) {
          looksLikeRow.push(el);
        }
      }
      if (looksLikeRow.length > 0) {
        rows = looksLikeRow;
        matchedSel = 'fallback: aria-label with date/email pattern';
      }
    }

    // Fallback 2: find a "Results" or "Top results" header, grab its sibling rows
    if (!rows.length) {
      const allEls = document.querySelectorAll('*');
      for (const el of allEls) {
        const t = (el.textContent || '').trim();
        if ((t === 'Top results' || t === 'Results' || t === 'Other results') && el.children.length === 0) {
          // Walk up to find the container, then look at the next siblings
          let container = el.parentElement;
          while (container && container !== document.body) {
            const childRows = container.querySelectorAll('div, li');
            const candidates = Array.from(childRows).filter(c => {
              const text = (c.textContent || '').trim();
              return text.length > 20 && text.length < 800 && c.offsetParent !== null;
            });
            if (candidates.length >= 2 && candidates.length < 50) {
              rows = candidates;
              matchedSel = 'fallback: "Results" header siblings';
              break;
            }
            container = container.parentElement;
          }
          if (rows.length) break;
        }
      }
    }

    console.log('[Lumen] listSearchResults — matched:', matchedSel, 'count:', rows.length);
    if (rows.length === 0) {
      // Debug: dump what selectors found anything
      console.log('[Lumen] Debug — selectors found:');
      rowSelectors.forEach(sel => {
        const n = document.querySelectorAll(sel).length;
        if (n > 0) console.log(`  ${sel}: ${n}`);
      });
      console.log('[Lumen] Page has elements with aria-label:', document.querySelectorAll('[aria-label]').length);
      console.log('[Lumen] Page has role=option:', document.querySelectorAll('[role="option"]').length);
      console.log('[Lumen] Page has data-convid:', document.querySelectorAll('[data-convid]').length);
    }
    if (rows.length > 0) {
      console.log('[Lumen] Sample row outerHTML (first 600 chars):',
        rows[0].outerHTML?.slice(0, 600));
    }

    if (!rows.length) {
      return {
        results: [],
        error: 'No result rows found in Outlook DOM. Open DevTools console on Outlook tab — share the [Lumen] log lines.'
      };
    }

    // Phrases that indicate a row is UI chrome, not an email
    const UI_CHROME_TEXTS = [
      'reading pane', 'nothing is selected', 'no email selected',
      'no items to display', 'no results', 'loading',
      'select an item', 'preview pane', 'focused', 'other',
    ];
    const isChromeRow = (text) => {
      const t = (text || '').trim().toLowerCase();
      if (!t) return true;
      return UI_CHROME_TEXTS.some(c => t.includes(c));
    };

    const results = [];
    const maxRows = Math.min(rows.length, 20);
    for (let i = 0; i < maxRows; i++) {
      const row = rows[i];
      const ariaLabel = row.getAttribute('aria-label') || '';
      const rowText = (row.textContent || '').slice(0, 500);

      // Skip rows whose text is clearly UI chrome (reading pane, "nothing selected", etc.)
      if (isChromeRow(rowText) || isChromeRow(ariaLabel)) continue;

      // Skip rows that don't have any email-row signals (date pattern, @, or aria with comma sep)
      const hasDateSignal = /\d{1,2}\/\d{1,2}|\d{1,2}:\d{2}\s*(AM|PM)|yesterday|today|monday|tuesday|wednesday|thursday|friday|saturday|sunday/i.test(rowText + ' ' + ariaLabel);
      const hasEmailSignal = /[\w.+-]+@[\w-]+\.[\w.-]+/.test(ariaLabel + ' ' + row.innerHTML.slice(0, 2000));
      const hasAriaCommaSep = ariaLabel.split(',').length >= 3;  // "Sender, Subject, Date, Folder"
      if (!hasDateSignal && !hasEmailSignal && !hasAriaCommaSep) continue;

      // Extract email from aria-label or any descendant title with @
      const emailMatch = (ariaLabel + ' ' + row.outerHTML.slice(0, 5000)).match(/[\w.+-]+@[\w-]+\.[\w.-]+/);
      const senderEmail = emailMatch ? emailMatch[0] : '';

      // Pull subject — prefer headings/data-testid, fall back to first prominent span
      let subject = '';
      const headingEl = row.querySelector('span[role="heading"], [data-testid*="ubject"], [data-testid*="Subject"], h3, [role="heading"]');
      if (headingEl) subject = clean(headingEl.textContent);

      // Pull sender — prefer title=@... or aria-label containing sender
      let sender = '';
      const senderEl = row.querySelector('span[title*="@"], [data-testid*="ender"], [data-testid*="Sender"]');
      if (senderEl) sender = clean(senderEl.textContent);

      // If we still don't have subject/sender, parse from aria-label
      // aria-label often looks like: "Sender Name, Subject text, Date, Folder"
      if ((!subject || !sender) && ariaLabel) {
        const parts = ariaLabel.split(/,\s+/);
        if (!sender && parts[0]) sender = clean(parts[0]);
        if (!subject && parts[1]) subject = clean(parts[1]);
      }

      // Reject rows where both fields are still unknown — they're not real email rows
      if ((!subject || subject === '(no subject)') && (!sender || sender === '(unknown)')) {
        continue;
      }
      // Reject rows whose sender is clearly chrome
      if (isChromeRow(sender) || isChromeRow(subject)) continue;

      // Snippet: last meaningful text in the row
      let snippet = '';
      const allText = row.innerText || row.textContent || '';
      const lines = allText.split('\n').map(t => clean(t)).filter(Boolean);
      for (let j = lines.length - 1; j >= 0; j--) {
        const t = lines[j];
        if (t.length > 15 && t !== subject && t !== sender && !/^\d/.test(t) && !isChromeRow(t)) {
          snippet = t.slice(0, 200);
          break;
        }
      }

      results.push({
        index: i,
        subject: subject || '(no subject)',
        sender: sender || '(unknown)',
        senderEmail,
        snippet,
      });
    }
    return { results };
  }

  function selectInboxRow(index) {
    const rows = document.querySelectorAll(
      'div[role="listbox"] div[role="option"], div[role="list"] div[role="listitem"]'
    );
    if (!rows.length || index < 0 || index >= rows.length) {
      return { ok: false, error: 'Row not found' };
    }
    rows[index].click();
    return { ok: true };
  }

  // Try a Reply button first (faster than New mail when replying)
  function clickReply() {
    const replyBtn = querySelectorWithFallbacks([
      'button[aria-label="Reply"]',
      'button[aria-label*="Reply"]:not([aria-label*="all"]):not([aria-label*="All"])',
      '[data-testid="reply"]',
    ]);
    if (replyBtn) {
      replyBtn.click();
      return true;
    }
    return false;
  }

  // ── Message bridge ─────────────────────────────────────────

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    try {
      switch (msg.action) {
        case 'getEmailContent':
          sendResponse({ ok: true, data: readCurrentEmail() });
          return false;
        case 'openCompose':
          sendResponse({ ok: openNewCompose() });
          return false;
        case 'clickReply':
          sendResponse({ ok: clickReply() });
          return false;
        case 'setTo':
          // setComposeTo is async now (it awaits chip-commit delays)
          setComposeTo(msg.email)
            .then(ok => sendResponse({ ok }))
            .catch(e => sendResponse({ ok: false, error: String(e) }));
          return true;
        case 'setSubject':
          sendResponse({ ok: setComposeSubject(msg.subject) });
          return false;
        case 'injectReply':
          sendResponse({ ok: injectReply(msg.text) });
          return false;
        case 'clickSendButton':
          // Async — verifies the compose pane actually closed after click
          clickSendButton()
            .then(ok => sendResponse({ ok }))
            .catch(e => sendResponse({ ok: false, error: String(e) }));
          return true;
        case 'searchInbox':
          // Async response — handle the await properly
          searchInbox(msg.query)
            .then(r => sendResponse(r))
            .catch(e => sendResponse({ ok: false, error: String(e) }));
          return true;
        case 'listSearchResults':
          sendResponse({ ok: true, ...listSearchResults() });
          return false;
        case 'selectInboxRow':
          sendResponse(selectInboxRow(msg.index));
          return false;
        case 'ping':
          sendResponse({ ok: true, page: location.href });
          return false;
        default:
          sendResponse({ ok: false, error: 'unknown action' });
          return false;
      }
    } catch (e) {
      sendResponse({ ok: false, error: String(e) });
      return false;
    }
  });

  // Signal that the content script loaded (helps debugging)
  console.log('[Lumen] content script loaded on', location.href);
})();
