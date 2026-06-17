// Lumen session — real backend, single-thread Lumen chat.
// Lumen is a tight abstraction: queries agents internally, answers inline.
// External launch (new tab) only on explicit "open X" requests.

import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { api } from '../lib/api.js'
import { seedBackendGraphToken } from '../lib/auth.js'

const now = () => new Date().toISOString()

const INITIAL_LUMEN = [
  { role: 'lumen', kind: 'text',
    content: "Hi, I\u2019m your Lumen \u2014 your personal learning companion. I know what you\u2019ve worked on across every subject. Ask me about your progress, schedule, peers, or what to learn next.",
    timestamp: now() },
]

function reducer(state, action) {
  switch (action.type) {
    case 'LUMEN_APPEND':
      return { ...state, lumenMessages: [...state.lumenMessages, action.message] }
    case 'LUMEN_REPLACE':
      return {
        ...state,
        lumenMessages: state.lumenMessages.map(m =>
          m.id === action.id ? { ...m, ...action.patch } : m),
      }
    case 'LUMEN_RESET':
      return { ...state, lumenMessages: action.messages || INITIAL_LUMEN }
    case 'NOTIFS_SET':
      return { ...state, notifications: action.items }
    case 'NOTIF_READ':
      return {
        ...state,
        notifications: state.notifications.map(n =>
          action.id === undefined || n.id === action.id ? { ...n, read: true } : n),
      }
    default:
      return state
  }
}

const INITIAL_STATE = { lumenMessages: INITIAL_LUMEN, notifications: [] }

export default function useLumenSession({ onNavigate, onExternalLaunch, v2 = false } = {}) {
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE)
  const [lumenTyping, setLumenTyping] = useState(false)
  const [lumenThreadId, setLumenThreadId] = useState(null)
  const voiceModeRef = useRef(false)
  // Ref that LumenChat populates with its onSpeakEnd callback
  const onSpeakEndRef = useRef(null)

  useEffect(() => {
    api.calNotifs()
      .then(r => {
        const items = (r?.notifications || []).map(n => ({
          id: n.id, kind: 'reminder',
          title: n.title || n.message || 'Reminder',
          when: n.when || '',
          read: !!n.read,
        }))
        if (items.length) dispatch({ type: 'NOTIFS_SET', items })
      })
      .catch(() => {})
  }, [])

  // Auto-load the most recent lumen thread on mount (or start fresh if none)
  useEffect(() => {
    api.threads('lumen')
      .then(threads => {
        if (threads && threads.length > 0) {
          const latest = threads[0]
          setLumenThreadId(latest.id)
          // Reload the latest thread's messages
          return api.thread(latest.id).then(data => {
            const msgs = (data?.messages || []).map(m => ({
              id: crypto.randomUUID(),
              role: m.role === 'user' ? 'user' : 'lumen',
              kind: 'text',
              content: m.content || '',
              timestamp: m.ts || m.created_at || now(),
            }))
            if (msgs.length > 0) {
              dispatch({ type: 'LUMEN_RESET', messages: msgs })
            }
          })
        }
      })
      .catch(() => {})
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const appendLumen = (msg) => dispatch({ type: 'LUMEN_APPEND', message: { id: crypto.randomUUID(), ...msg } })

  // Load a thread's messages into the chat view
  const loadThread = useCallback(async (threadId) => {
    if (!threadId) return
    setLumenTyping(true)
    try {
      const data = await api.thread(threadId)
      const msgs = (data?.messages || []).map(m => ({
        id: crypto.randomUUID(),
        role: m.role === 'user' ? 'user' : 'lumen',
        kind: 'text',
        content: m.content || '',
        timestamp: m.ts || m.created_at || now(),
      }))
      if (msgs.length > 0) {
        dispatch({ type: 'LUMEN_RESET', messages: msgs })
        setLumenThreadId(threadId)
      }
    } catch {}
    finally { setLumenTyping(false) }
  }, [])

  // Start a fresh thread
  const newThread = useCallback(() => {
    setLumenThreadId(null)
    dispatch({ type: 'LUMEN_RESET', messages: INITIAL_LUMEN })
  }, [])

  const sendLumen = useCallback(async (text, { fromVoice = false, file = null } = {}) => {
    if (!text?.trim() && !file) return
    voiceModeRef.current = fromVoice

    // Show user bubble — include file name hint if file attached
    const userContent = file ? `${text || ''}${text ? ' ' : ''}📎 ${file.name}` : text
    appendLumen({ role: 'user', kind: 'text', content: userContent, timestamp: now() })
    setLumenTyping(true)

    // If a file is attached, route through /chat/file-message
    if (file) {
      try {
        const result = await api.fileMessage(file, text || '', null, lumenThreadId)
        appendLumen({
          role: 'lumen', kind: 'text',
          content: result?.reply || 'File processed.',
          timestamp: now(),
          action: result?.action,
          agent_id: result?.agent_id,
          cards: result?.cards || [],
        })
        if (result?.thread_id) setLumenThreadId(result.thread_id)
      } catch (e) {
        appendLumen({
          role: 'lumen', kind: 'text', timestamp: now(),
          content: `Couldn't process file: ${e?.message || 'unknown error'}`,
        })
      } finally {
        setLumenTyping(false)
      }
      return
    }

    // Silently acquire Graph token if query looks like Outlook/OneDrive
    let graphToken = null
    const msgLower = (text || '').toLowerCase()
    const graphKw = [
      'mail', 'email', 'inbox', 'outlook', 'onedrive', 'drive', 'files', 'shared with me',
      'recent files', 'my files', 'conference room', 'inbox rules', 'categories', 'email headers',
      'email changes', 'important mail', 'high importance', 'create folder', 'search my drive',
    ]
    if (graphKw.some(kw => msgLower.includes(kw))) {
      try {
        const { getGraphToken } = await import('../lib/auth.js')
        // Silent only — NEVER popup during chat (would open popup-inside-panel).
        // If no cached token, backend uses server-side seeded token or returns a
        // friendly "connect your account" message.
        graphToken = await getGraphToken(false)
      } catch (e) {
        console.warn('Graph token acquisition failed:', e?.message)
      }
    }

    // ── Lumen v2 (Magentic-One) path ──────────────────────────────────────
    // v2 has no SSE endpoint; use the REST /v2/chat orchestrator. The response
    // carries the same core fields as v1 plus a `turns` orchestration trace.
    if (v2) {
      try {
        const chat = await api.lumenChatV2(text, lumenThreadId, graphToken)
        if (chat?.thread_id) setLumenThreadId(chat.thread_id)
        appendLumen({
          role: 'lumen', kind: 'text',
          content: chat?.reply || "I’m not sure yet — try asking differently.",
          timestamp: now(),
          action: chat?.action,
          agent_id: chat?.agent_id,
          cards: chat?.cards || [],
          turns: chat?.turns || [],
        })
      } catch (err) {
        appendLumen({
          role: 'lumen', kind: 'text', timestamp: now(),
          content: "I couldn’t reach Lumen v2. Your session may have expired — try signing in again.",
        })
        console.error(err)
      } finally {
        setLumenTyping(false)
      }
      return
    }

    // Pre-open a blank tab for launch commands (popup blocker workaround)
    const looksLikeLaunch = /^(open|go\s*to|switch\s*to|launch|take\s*me\s*to)\b/i.test(text.trim())
    const popup = looksLikeLaunch ? window.open('about:blank', '_blank') : null

    // Generate a placeholder message ID for streaming updates
    const msgId = crypto.randomUUID()

    try {
      // Try AG-UI SSE streaming first
      const token = localStorage.getItem('lumen.token') || localStorage.getItem('lumen_token')
      const resp = await fetch('/ag-ui/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
        body: JSON.stringify({ message: text, thread_id: lumenThreadId, graph_token: graphToken }),
      })

      if (!resp.ok || !resp.body) {
        // Fallback to REST
        throw new Error('SSE not available')
      }

      // Stream SSE events
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let streamedText = ''
      let meta = {}
      let a2uiDoc = null
      let messageAppended = false

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // Parse SSE lines
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          try {
            const event = JSON.parse(line.slice(6))

            switch (event.type) {
              case 'TEXT_MESSAGE_START':
                // Append initial empty message for streaming
                if (!messageAppended) {
                  appendLumen({
                    role: 'lumen', kind: 'text', content: '',
                    timestamp: now(), id: msgId,
                  })
                  messageAppended = true
                }
                break

              case 'TEXT_MESSAGE_CONTENT':
                streamedText += event.delta || ''
                // Update the message progressively
                dispatch({ type: 'LUMEN_REPLACE', id: msgId, patch: { content: streamedText } })
                break

              case 'TEXT_MESSAGE_END':
                break

              case 'CUSTOM':
                if (event.name === 'a2ui' && event.value) {
                  a2uiDoc = event.value
                }
                break

              case 'STATE_SNAPSHOT':
                meta = event.snapshot || {}
                break

              case 'RUN_STARTED':
                if (event.threadId) setLumenThreadId(event.threadId)
                break
            }
          } catch {}
        }
      }

      // Final update with all metadata
      dispatch({
        type: 'LUMEN_REPLACE', id: msgId,
        patch: {
          content: streamedText || "I\u2019m not sure yet \u2014 try asking differently.",
          action: meta.action,
          agent_id: meta.agent_id,
          cards: meta.cards || [],
          proposal: meta.proposal || null,
          redirect_url: meta.redirect_url,
          a2ui: a2uiDoc || null,
        },
      })
      if (meta.thread_id) setLumenThreadId(meta.thread_id)

      // Handle launches/navigation.
      //  external_launch -> open in a new tab (reuse the pre-opened popup if any).
      //  context_switch / plain redirect -> navigate IN-APP via the client router.
      // Never point a browser tab at a backend route (it renders the API's 404 JSON).
      const isExternal = meta.action === 'external_launch' && meta.redirect_url
      if (isExternal && popup && !popup.closed) {
        popup.location = meta.redirect_url
      } else if (isExternal && typeof onExternalLaunch === 'function') {
        if (popup && !popup.closed) popup.close()
        onExternalLaunch(meta.redirect_url)
      } else {
        if (popup && !popup.closed) popup.close()
        if (meta.redirect_url && typeof onNavigate === 'function') {
          setTimeout(() => onNavigate(meta.redirect_url), 400)
        }
      }

    } catch (err) {
      // Fallback to REST API
      if (popup && !popup.closed) popup.close()
      try {
        const chat = await api.lumenChat(text, lumenThreadId, graphToken)
        if (chat?.thread_id) setLumenThreadId(chat.thread_id)
        appendLumen({
          role: 'lumen', kind: 'text',
          content: chat?.reply || "I\u2019m not sure yet \u2014 try asking differently.",
          timestamp: now(),
          action: chat?.action,
          agent_id: chat?.agent_id,
          proposal: chat?.proposal,
          cards: chat?.cards || [],
          redirect_url: chat?.redirect_url,
          a2ui: chat?.a2ui || null,
        })

        const isExternal = chat?.action === 'external_launch' && chat?.redirect_url
        if (isExternal && popup && !popup.closed) {
          popup.location = chat.redirect_url
        } else if (isExternal && typeof onExternalLaunch === 'function') {
          if (popup && !popup.closed) popup.close()
          onExternalLaunch(chat.redirect_url)
        } else {
          if (popup && !popup.closed) popup.close()
          if (chat?.redirect_url && typeof onNavigate === 'function') {
            setTimeout(() => onNavigate(chat.redirect_url), 400)
          }
        }
      } catch (err2) {
        appendLumen({
          role: 'lumen', kind: 'text', timestamp: now(),
          content: "I couldn\u2019t reach the server. Your session may have expired \u2014 try signing in again.",
        })
        console.error(err2)
      }
    } finally {
      setLumenTyping(false)
    }
  }, [lumenThreadId, onNavigate, onExternalLaunch, v2])

  const confirmProposal = useCallback(async (messageId, proposal, accept) => {
    dispatch({
      type: 'LUMEN_REPLACE',
      id: messageId,
      patch: { proposalResolved: accept ? 'accepted' : 'declined' },
    })
    if (!accept) {
      appendLumen({ role: 'lumen', kind: 'text', timestamp: now(), content: 'Okay \u2014 discarded the plan. Let me know if you want a different one.' })
      return
    }
    setLumenTyping(true)
    try {
      const r = await api.confirmPlan(proposal)
      const n = r?.count || 0
      const events = r?.events || []
      const lines = [`Scheduled **${n}** study session${n === 1 ? '' : 's'} on your calendar:`]
      for (const ev of events) {
        lines.push(`- **${ev.title}** \u2014 ${ev.date} at ${ev.time}`)
      }
      appendLumen({ role: 'lumen', kind: 'text', timestamp: now(), content: lines.join('\n') })
    } catch (err) {
      appendLumen({ role: 'lumen', kind: 'text', timestamp: now(), content: "I couldn\u2019t schedule those. Try asking me again." })
    } finally {
      setLumenTyping(false)
    }
  }, [])

  const markNotifRead = useCallback((id) =>
    dispatch({ type: 'NOTIF_READ', id }), [])

  // ── UX Preset state + effects ─────────────────────────────
  const [uxPreset, setUxPreset] = useState(null)
  const ttsRef = useRef(null)

  // Load preset on mount
  useEffect(() => {
    api.uxGet().then(r => {
      if (r?.active) {
        setUxPreset(r.active)
        applyPresetVisuals(r.active)
      }
    }).catch(() => {})
  }, [])

  // Apply visual CSS classes based on preset
  function applyPresetVisuals(preset) {
    const root = document.documentElement
    root.classList.remove('ux-vision', 'ux-minimal', 'ux-data', 'ux-audio')
    if (preset?.id === 'vision') {
      root.classList.add('ux-vision')
      root.style.fontSize = '18px'
    } else if (preset?.id === 'minimal') {
      root.classList.add('ux-minimal')
      root.style.fontSize = ''
    } else if (preset?.id === 'data-focused') {
      root.classList.add('ux-data')
      root.style.fontSize = ''
    } else if (preset?.id === 'audio-first') {
      root.classList.add('ux-audio')
      root.style.fontSize = ''
    } else {
      root.style.fontSize = ''
    }
  }

  // TTS: speak text aloud using Azure Neural TTS (falls back to browser)
  async function speakText(text) {
    try {
      const { azureSpeak } = await import('./useAzureSpeech.js')
      await azureSpeak(text, {
        onEnd: () => onSpeakEndRef.current?.(),
        // Barge-in: if user speaks during TTS, stop it and send their new message
        onBargeIn: voiceModeRef.current
          ? (bargeText) => {
              onSpeakEndRef.current?.() // restart mic
              if (bargeText?.trim()) sendLumen(bargeText, { fromVoice: true })
            }
          : undefined,
      })
    } catch {
      // Final fallback: browser speechSynthesis
      if (!window.speechSynthesis) return
      window.speechSynthesis.cancel()
      const clean = text.replace(/[*#_`]/g, '').replace(/\n+/g, '. ')
      const utt = new SpeechSynthesisUtterance(clean)
      utt.rate = 1.0; utt.lang = 'en-US'
      utt.onend = () => onSpeakEndRef.current?.()
      ttsRef.current = utt
      window.speechSynthesis.speak(utt)
    }
  }

  // Watch for preset changes in chat responses
  useEffect(() => {
    const last = state.lumenMessages[state.lumenMessages.length - 1]
    if (!last || last.role !== 'lumen') return

    // If UX preset was just changed via chat
    let activePreset = uxPreset
    if (last.action === 'ux_preset_changed' && last.cards?.[0]?.data) {
      const newPreset = last.cards[0].data
      setUxPreset(newPreset)
      applyPresetVisuals(newPreset)
      activePreset = newPreset
    }

    // TTS: auto-speak Lumen responses if tts_enabled OR last message was voice
    if ((activePreset?.tts_enabled || voiceModeRef.current) && last.content) {
      speakText(last.content)
    }
  }, [state.lumenMessages.length])

  return {
    lumenMessages: state.lumenMessages,
    notifications: state.notifications,
    lumenTyping,
    lumenThreadId,
    sendLumen,
    appendLumen,
    confirmProposal,
    markNotifRead,
    loadThread,
    newThread,
    uxPreset,
    voiceModeRef,
    onSpeakEndRef,
  }
}
