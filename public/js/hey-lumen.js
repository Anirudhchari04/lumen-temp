/**
 * Hey Lumen — Global wake word listener.
 * Loaded on all pages (Lumen SPA, TAs, Calendar).
 * Listens for "hey lumen" trigger → captures command → sends to Lumen chat API.
 *
 * Usage: <script src="/js/hey-lumen.js"></script>
 * Toggle: window.heyLumen.enable() / .disable() / .toggle()
 */
(function () {
  'use strict';

  const WAKE_PHRASES = ['hey lumen', 'hi lumen', 'ok lumen', 'okay lumen'];
  // Hard cap for the entire command recording window.
  const COMMAND_TIMEOUT_MS = 12000;
  // After a final/interim transcript, wait this long for more speech before
  // assuming the user is done. Tuned for natural sentence pauses.
  const SILENCE_END_MS = 1200;

  let enabled = false;
  let wakeRecognizer = null;     // continuous recognizer that watches for wake phrase
  let commandRecognizer = null;  // single-utterance recognizer that captures the command
  let state = 'idle'; // idle | listening | command | processing
  let indicator = null;

  // ── UI: floating mic indicator ──
  function createIndicator() {
    if (indicator) return;
    indicator = document.createElement('div');
    indicator.id = 'hey-lumen-indicator';
    indicator.innerHTML = `
      <style>
        #hey-lumen-indicator {
          position: fixed; bottom: 80px; right: 20px; z-index: 99999;
          display: flex; align-items: center; gap: 8px;
          background: #2c2820; color: #faf8f4; padding: 8px 14px;
          border-radius: 24px; font-size: 12px; font-family: system-ui, sans-serif;
          box-shadow: 0 4px 20px rgba(0,0,0,0.2); transition: all 0.3s;
          cursor: pointer; user-select: none;
        }
        #hey-lumen-indicator.idle { opacity: 0.5; }
        #hey-lumen-indicator.listening { opacity: 0.8; }
        #hey-lumen-indicator.command { opacity: 1; background: #c0392b; }
        #hey-lumen-indicator.processing { opacity: 1; background: #d4a853; color: #2c2820; }
        #hey-lumen-dot {
          width: 10px; height: 10px; border-radius: 50%; background: #666;
          transition: background 0.3s;
        }
        #hey-lumen-indicator.listening #hey-lumen-dot { background: #888; }
        #hey-lumen-indicator.command #hey-lumen-dot {
          background: #fff; animation: hl-pulse 0.8s ease-in-out infinite;
        }
        #hey-lumen-indicator.processing #hey-lumen-dot { background: #2c2820; }
        @keyframes hl-pulse {
          0%, 100% { transform: scale(1); opacity: 1; }
          50% { transform: scale(1.4); opacity: 0.6; }
        }
      </style>
      <span id="hey-lumen-dot"></span>
      <span id="hey-lumen-label">Say "Hey Lumen"</span>
    `;
    indicator.onclick = () => window.heyLumen.toggle();
    document.body.appendChild(indicator);
  }

  function updateUI(newState, label) {
    state = newState;
    if (!indicator) return;
    indicator.className = newState;
    const lbl = indicator.querySelector('#hey-lumen-label');
    if (lbl) lbl.textContent = label || {
      idle: 'Say "Hey Lumen"',
      listening: 'Listening for "Hey Lumen"...',
      command: '🔴 Recording command...',
      processing: 'Processing...',
    }[newState] || '';
  }

  // ── Send command to Lumen ──
  async function sendToLumen(command) {
    updateUI('processing', 'Sending: ' + command.slice(0, 30) + '...');
    const token = localStorage.getItem('lumen.token') || localStorage.getItem('lumen_token');
    if (!token) {
      updateUI('listening');
      return;
    }
    try {
      const r = await fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token },
        body: JSON.stringify({ message: command }),
      });
      const data = await r.json();
      const reply = data.reply || 'Done.';

      // Speak the reply if TTS is available
      if (window.speechSynthesis) {
        window.speechSynthesis.cancel();
        const clean = reply.replace(/[*#_`]/g, '').replace(/\n+/g, '. ');
        const utt = new SpeechSynthesisUtterance(clean);
        utt.rate = 1.0; utt.lang = 'en-US';
        window.speechSynthesis.speak(utt);
      }

      // Show toast notification with the reply
      showToast(reply);
    } catch (e) {
      showToast('Could not reach Lumen: ' + e.message);
    }
    updateUI('listening');
    // Resume wake-word listening for the next command.
    if (enabled && !wakeRecognizer && !commandRecognizer) startWakeRecognizer();
  }

  function showToast(text) {
    const toast = document.createElement('div');
    toast.style.cssText = `
      position: fixed; bottom: 130px; right: 20px; z-index: 99998;
      background: #2c2820; color: #faf8f4; padding: 12px 18px;
      border-radius: 12px; font-size: 13px; max-width: 350px;
      box-shadow: 0 4px 20px rgba(0,0,0,0.3); font-family: system-ui;
      animation: hl-toast-in 0.3s ease-out;
    `;
    const style = document.createElement('style');
    style.textContent = `
      @keyframes hl-toast-in { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; } }
    `;
    toast.appendChild(style);
    toast.textContent = text.slice(0, 200);
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 6000);
  }

  // ── Speech Recognition ──
  // Strategy:
  //   1. Wake recognizer runs continuously, watching for a wake phrase.
  //   2. When a wake phrase is heard, stop the wake recognizer and start a
  //      fresh, single-utterance recognizer for the command. This recognizer
  //      naturally fires `isFinal` once the user stops speaking (more reliable
  //      than continuous mode).
  //   3. Command recognizer also has a silence-based fallback: if interim
  //      results stop arriving for SILENCE_END_MS, send what we have.
  //   4. After sending (or timeout/error), restart the wake recognizer.

  function getSpeechRec() {
    return window.SpeechRecognition || window.webkitSpeechRecognition;
  }

  function startWakeRecognizer() {
    const SpeechRec = getSpeechRec();
    if (!SpeechRec) {
      console.warn('Hey Lumen: Speech recognition not supported');
      return;
    }

    const rec = new SpeechRec();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = 'en-US';
    wakeRecognizer = rec;

    rec.onresult = (event) => {
      // Walk newest results and look for a wake phrase.
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const r = event.results[i];
        const transcript = r[0].transcript.toLowerCase().trim();
        for (const phrase of WAKE_PHRASES) {
          const idx = transcript.indexOf(phrase);
          if (idx === -1) continue;
          const afterWake = transcript.slice(idx + phrase.length).trim();
          // Stop the wake recognizer; we'll either send the inline command
          // immediately or hand off to the command recognizer.
          stopWakeRecognizer();
          if (afterWake && afterWake.length > 1 && r.isFinal) {
            // Wake phrase + command in one breath — send right away.
            sendToLumen(afterWake);
          } else {
            // Just wake — capture the command in a fresh single-utterance pass.
            startCommandRecognizer(afterWake);
          }
          return;
        }
      }
    };

    rec.onend = () => {
      // Auto-restart wake listening unless we've moved on to command mode
      // or been disabled. (commandRecognizer takes over the mic exclusively.)
      if (!enabled) return;
      if (commandRecognizer) return;
      if (state === 'processing') return;
      try { rec.start(); } catch {}
      if (state !== 'command') updateUI('listening');
    };

    rec.onerror = (e) => {
      if (e.error === 'not-allowed') {
        updateUI('idle', 'Mic blocked — click to retry');
        enabled = false;
        return;
      }
      // Transient — onend will retry.
    };

    try {
      rec.start();
      updateUI('listening');
    } catch (e) {
      console.error('Hey Lumen: failed to start wake recognizer', e);
    }
  }

  function stopWakeRecognizer() {
    if (!wakeRecognizer) return;
    try { wakeRecognizer.onend = null; wakeRecognizer.stop(); } catch {}
    wakeRecognizer = null;
  }

  function startCommandRecognizer(seedText) {
    const SpeechRec = getSpeechRec();
    if (!SpeechRec) return;

    updateUI('command', '🔴 Recording…');

    const rec = new SpeechRec();
    rec.continuous = false;       // single utterance — fires isFinal on natural pause
    rec.interimResults = true;
    rec.lang = 'en-US';
    commandRecognizer = rec;

    let interim = seedText || '';
    let lastInterim = '';
    let silenceTimer = null;
    let hardTimer = null;
    let finished = false;

    const finalize = (textOverride) => {
      if (finished) return;
      finished = true;
      if (silenceTimer) { clearTimeout(silenceTimer); silenceTimer = null; }
      if (hardTimer) { clearTimeout(hardTimer); hardTimer = null; }
      try { rec.onresult = null; rec.onerror = null; rec.onend = null; rec.stop(); } catch {}
      commandRecognizer = null;
      const cmd = (textOverride ?? interim).trim();
      if (cmd) {
        sendToLumen(cmd);
      } else {
        updateUI('listening');
        if (enabled && !wakeRecognizer) startWakeRecognizer();
      }
    };

    rec.onresult = (event) => {
      let combined = seedText ? seedText + ' ' : '';
      for (let i = 0; i < event.results.length; i++) {
        combined += event.results[i][0].transcript;
      }
      combined = combined.trim();
      if (combined && combined !== lastInterim) {
        lastInterim = combined;
        interim = combined;
        // Show what we're capturing.
        updateUI('command', '🔴 ' + combined.slice(0, 40));
        // Reset silence timer on new speech.
        if (silenceTimer) clearTimeout(silenceTimer);
        silenceTimer = setTimeout(() => finalize(), SILENCE_END_MS);
      }
      // If the browser already gave us a final result, send immediately.
      const last = event.results[event.results.length - 1];
      if (last && last.isFinal) {
        finalize(combined);
      }
    };

    rec.onerror = (e) => {
      if (e.error === 'not-allowed') {
        finished = true;
        commandRecognizer = null;
        updateUI('idle', 'Mic blocked — click to retry');
        enabled = false;
        return;
      }
      // No-speech / aborted — go back to listening.
      finalize();
    };

    rec.onend = () => {
      // If the browser ended the recognizer without firing a final result,
      // finalize with whatever interim we have.
      if (!finished) finalize();
    };

    // Hard cap so a stuck recognizer can't trap us.
    hardTimer = setTimeout(() => finalize(), COMMAND_TIMEOUT_MS);
    // Initial silence timer — if no speech at all, drop back to listening.
    silenceTimer = setTimeout(() => finalize(), SILENCE_END_MS + 1500);

    try {
      rec.start();
    } catch (e) {
      console.error('Hey Lumen: failed to start command recognizer', e);
      finalize();
    }
  }

  function stopListening() {
    stopWakeRecognizer();
    if (commandRecognizer) {
      try { commandRecognizer.stop(); } catch {}
      commandRecognizer = null;
    }
    updateUI('idle', 'Hey Lumen (off)');
  }

  // ── Public API ──
  window.heyLumen = {
    enable() {
      enabled = true;
      createIndicator();
      startWakeRecognizer();
    },
    disable() {
      enabled = false;
      stopListening();
    },
    toggle() {
      if (enabled) this.disable();
      else this.enable();
    },
    isEnabled() { return enabled; },
    getState() { return state; },
  };

  // Auto-enable if user has TTS/STT preset
  document.addEventListener('DOMContentLoaded', () => {
    createIndicator();
    // Check if user has voice preset active
    const token = localStorage.getItem('lumen.token') || localStorage.getItem('lumen_token');
    if (token) {
      fetch('/lumen/ux', { headers: { 'Authorization': 'Bearer ' + token } })
        .then(r => r.json())
        .then(d => {
          if (d?.active?.stt_enabled) {
            window.heyLumen.enable();
          }
        })
        .catch(() => {});
    }
  });
})();
