import { useCallback, useEffect, useRef, useState } from 'react'
import MessageBubble from './MessageBubble.jsx'
import QuickChips from './QuickChips.jsx'
import LoadingDots from './LoadingDots.jsx'
import { IconArrowUp, IconCheck, IconClose } from './icons.jsx'
import { getGraphToken, getSmtpToken, githubConnect, googleConnect,
         githubDisconnect, googleDisconnect, notionDisconnect } from '../lib/auth.js'
import { api } from '../lib/api.js'
import A2UISurface from './A2UISurface.jsx'
import useAzureSpeech from '../hooks/useAzureSpeech.js'

const CHIPS = [
  'How am I doing?',
  'Show my peers',
  'Show my GitHub portfolio',
  'Show my recent GitHub actions',
  'What\u2019s on my calendar?',
  'Make a study plan',
  'What should I learn next?',
]

export default function LumenChat({ messages = [], onSend, typing = false, onConfirmProposal, uxPreset, onSpeakEndRef, overrideChips }) {
  const [value, setValue] = useState('')
  const [interimText, setInterimText] = useState('')
  const [pendingFile, setPendingFile] = useState(null) // { file, previewUrl, name }
  const fileInputRef = useRef(null)
  const endRef = useRef(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length, typing])

  const submit = (e) => {
    e?.preventDefault?.()
    const text = value.trim()
    if (!text && !pendingFile) return
    onSend?.(text || (pendingFile ? 'Uploaded file' : ''), { fromVoice: false, file: pendingFile?.file || null })
    setValue('')
    setInterimText('')
    if (pendingFile?.previewUrl) URL.revokeObjectURL(pendingFile.previewUrl)
    setPendingFile(null)
  }

  const handleFileSelect = (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    const previewUrl = file.type.startsWith('image/') ? URL.createObjectURL(file) : null
    setPendingFile({ file, previewUrl, name: file.name })
    e.target.value = ''
  }

  // Azure Speech: interactive voice loop
  const { listening, voiceLiveMode, startListening, stopVoiceLive, onSpeakEnd, azureAvailable } = useAzureSpeech({
    onResult: useCallback((transcript) => {
      setInterimText('')
      if (transcript.trim()) onSend?.(transcript.trim(), { fromVoice: true })
    }, [onSend]),
    onInterim: useCallback((text) => setInterimText(text), []),
    onError: useCallback((err) => { console.warn('Speech error:', err); setInterimText('') }, []),
  })

  // Expose onSpeakEnd so useLumenSession can call it after TTS finishes
  useEffect(() => {
    if (onSpeakEndRef) onSpeakEndRef.current = onSpeakEnd
  }, [onSpeakEnd, onSpeakEndRef])

  const isVoiceLive = voiceLiveMode || listening

  return (
    <section className="flex-1 min-w-0 flex flex-col" style={{ background: 'transparent' }}>
      {/* Voice Live active banner */}
      {isVoiceLive && (
        <div className="px-5 py-2.5 bg-gradient-to-r from-blue-50 to-blue-50/60 border-b border-blue-100 flex items-center gap-2.5">
          <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse shrink-0" />
          <span className="text-[12.5px] font-medium text-blue-700">
            {listening ? 'Listening…' : 'Voice Live — speak anytime'}
          </span>
          {interimText && (
            <span className="italic text-[12px] text-blue-500/80 truncate flex-1">"{interimText}"</span>
          )}
          <button
            type="button"
            onClick={stopVoiceLive}
            className="ml-auto text-[11px] text-blue-400 hover:text-blue-600 shrink-0 transition-colors"
          >
            Stop
          </button>
        </div>
      )}
      {/* Chat messages */}
      <div className="flex-1 min-h-0 overflow-y-auto px-5 py-5 flex flex-col gap-3.5" style={{ background: 'rgba(0,0,0,0.018)' }}>
        {messages.map((m, i) => (
          <div key={m.id || i}>
            {m.role !== 'user' && m.agent_id && m.agent_id !== 'lumen' && (
              <AgentBadge agentId={m.agent_id} />
            )}
            <MessageBubble
              role={m.role === 'user' ? 'user' : 'lumen'}
              content={m.content}
              timestamp={m.timestamp}
            >
              {m.action === 'study_plan_proposal' && Array.isArray(m.proposal) && m.proposal.length > 0 && (
                <ProposalActions
                  resolved={m.proposalResolved}
                  onAccept={() => onConfirmProposal?.(m.id, m.proposal, true)}
                  onDecline={() => onConfirmProposal?.(m.id, m.proposal, false)}
                />
              )}
            </MessageBubble>
            {/* Rich inline cards */}
            {Array.isArray(m.cards) && m.cards.length > 0 && (
              <div className="mt-2.5 flex flex-col gap-2 ml-0 max-w-[82%]">
                {m.cards.map((card, ci) => <InlineCard key={ci} card={card} onSend={onSend} />)}
              </div>
            )}
            {/* A2UI generative UI */}
            {m.a2ui && (
              <div className="mt-2.5 ml-0 max-w-[90%]">
                <A2UISurface document={m.a2ui} onAction={onSend} />
              </div>
            )}
          </div>
        ))}
        {typing && <LoadingDots label="Lumen is thinking\u2026" />}
        <div ref={endRef} />
      </div>

      <QuickChips chips={overrideChips || CHIPS} onPick={(t) => onSend?.(t, { fromVoice: false })} />

      {/* Interim voice transcript preview */}
      {interimText && !isVoiceLive && (
        <div className="px-5 pb-1.5 text-[12px] text-ink dark:text-white-muted/70 italic tracking-wide">
          {interimText}…
        </div>
      )}

      {/* Pending file preview strip */}
      {pendingFile && (
        <div className="px-5 pb-2 flex items-center gap-2.5">
          <div className="relative shrink-0">
            {pendingFile.previewUrl
              ? <img src={pendingFile.previewUrl} alt="preview"
                  className="w-12 h-12 rounded-xl object-cover border border-beige-200 dark:border-gray-700 shadow-sm" />
              : <div className="w-12 h-12 rounded-xl bg-beige-100 dark:bg-gray-800 border border-beige-200 dark:border-gray-700 flex items-center justify-center text-lg shadow-sm">📎</div>
            }
          </div>
          <div className="flex-1 min-w-0">
            <span className="text-[12px] text-ink dark:text-white font-medium truncate block">{pendingFile.name}</span>
            <span className="text-[11px] text-ink dark:text-white-muted">Ready to send</span>
          </div>
          <button
            type="button"
            onClick={() => { URL.revokeObjectURL(pendingFile.previewUrl); setPendingFile(null) }}
            className="w-6 h-6 rounded-full bg-beige-200 hover:bg-beige-300 text-ink dark:text-white-muted hover:text-ink dark:text-white flex items-center justify-center text-[11px] transition-colors"
            title="Remove file"
          >✕</button>
        </div>
      )}

      {/* Input bar */}
      <form onSubmit={submit} className="px-4 pb-5 pt-2 flex items-end gap-2" style={{ borderTop: '0.5px solid rgba(0,0,0,0.05)' }}>
        {/* Hidden file input */}
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*,application/pdf,video/*"
          className="hidden"
          onChange={handleFileSelect}
        />

        {/* Attach button */}
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          title="Attach file"
          aria-label="Attach file"
          className="glass-input w-9 h-9 rounded-xl flex items-center justify-center transition-all duration-150 shrink-0 text-ink dark:text-white-soft hover:text-ink dark:text-white active:scale-95 text-sm mb-0.5"
          style={{ boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}
        >
          📎
        </button>

        {/* Text input */}
        <div className="flex-1 relative">
          <input
            value={value}
            onChange={e => setValue(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) submit(e) }}
            placeholder={listening ? 'Listening… or type here' : 'Ask Lumen anything…'}
            className="w-full rounded-2xl px-4 py-2.5 text-[13.5px] text-ink dark:text-white outline-none placeholder:text-ink dark:text-white-muted/50 transition-all duration-150 glass-input"
            style={{ boxShadow: '0 1px 4px rgba(0,0,0,0.06)' }}
            onFocus={e => { e.target.style.boxShadow = '0 0 0 3px rgba(200,118,42,0.12), 0 1px 4px rgba(0,0,0,0.06)' }}
            onBlur={e => { e.target.style.boxShadow = '0 1px 4px rgba(0,0,0,0.06)' }}
          />
        </div>

        {/* Voice toggle */}
        <button
          type="button"
          onClick={isVoiceLive ? stopVoiceLive : startListening}
          title={isVoiceLive ? 'Stop voice' : 'Start voice input'}
          aria-label={isVoiceLive ? 'Stop voice' : 'Voice input'}
          className={`w-9 h-9 rounded-xl flex items-center justify-center transition-all duration-150 text-sm shrink-0 mb-0.5 ${
            isVoiceLive && listening
              ? 'bg-blue-500 text-white shadow-sm shadow-blue-200 animate-pulse'
              : isVoiceLive
                ? 'bg-blue-100 text-blue-600'
                : azureAvailable
                  ? 'bg-beige-100 dark:bg-gray-800 text-blue-500 hover:bg-blue-50 hover:text-blue-600'
                  : 'bg-beige-100 dark:bg-gray-800 text-ink dark:text-white-soft hover:bg-beige-200'
          } active:scale-95`}
        >
          {isVoiceLive ? (listening ? '🔴' : '🎙️') : (azureAvailable ? '🎙️' : '🎤')}
        </button>

        {/* Send button */}
        <button
          type="submit"
          aria-label="Send message"
          disabled={!value.trim() && !pendingFile}
          className="w-9 h-9 rounded-xl text-white flex items-center justify-center active:scale-95 disabled:opacity-40 disabled:cursor-not-allowed transition-all duration-150 shrink-0 mb-0.5"
          style={{ background: '#c8762a', boxShadow: '0 2px 8px rgba(200,118,42,0.32)' }}
        >
          <IconArrowUp />
        </button>
      </form>
    </section>
  )
}

function ProposalActions({ resolved, onAccept, onDecline }) {
  if (resolved === 'accepted') {
    return (
      <div className="mt-2.5 text-[11.5px] text-emerald-700 flex items-center gap-1.5 font-medium">
        <IconCheck /> Scheduled
      </div>
    )
  }
  if (resolved === 'declined') {
    return (
      <div className="mt-2.5 text-[11.5px] text-ink dark:text-white-muted italic">Discarded</div>
    )
  }
  return (
    <div className="mt-3 flex items-center gap-2">
      <button onClick={onAccept}
        className="inline-flex items-center gap-1.5 text-[12px] px-3.5 py-1.5 rounded-xl bg-amber text-white hover:bg-amber/90 active:scale-95 transition-all shadow-sm font-medium">
        <IconCheck /> Yes, schedule
      </button>
      <button onClick={onDecline}
        className="inline-flex items-center gap-1.5 text-[12px] px-3.5 py-1.5 rounded-xl bg-beige-100 dark:bg-gray-800 border border-beige-200 dark:border-gray-700 text-ink dark:text-white-soft hover:bg-beige-200 active:scale-95 transition-all">
        <IconClose /> Discard
      </button>
    </div>
  )
}

// ── Contextual agent badge — shows which sub-agent handled a response ────

const AGENT_META = {
  // agent_id → { label, icon, tone }
  lumen:         { label: 'Lumen',         icon: '✦', tone: 'bg-amber/15 text-amber-700 dark:text-amber-300' },
  communication: { label: 'Comms agent',   icon: '✉️', tone: 'bg-blue-500/15 text-blue-700 dark:text-blue-300' },
  gmail:         { label: 'Comms agent',   icon: '✉️', tone: 'bg-blue-500/15 text-blue-700 dark:text-blue-300' },
  outlook:       { label: 'Outlook',       icon: '📨', tone: 'bg-blue-600/15 text-blue-800 dark:text-blue-300' },
  notion:        { label: 'Notion',        icon: '📓', tone: 'bg-zinc-500/15 text-zinc-700 dark:text-zinc-300' },
  drive:         { label: 'Drive',         icon: '📁', tone: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300' },
  gcalendar:     { label: 'Google Calendar', icon: '📅', tone: 'bg-rose-500/15 text-rose-700 dark:text-rose-300' },
  calendar:      { label: 'Calendar',      icon: '📅', tone: 'bg-rose-500/15 text-rose-700 dark:text-rose-300' },
  portfolio:     { label: 'Portfolio',     icon: '💼', tone: 'bg-violet-500/15 text-violet-700 dark:text-violet-300' },
  shiksha:       { label: 'Shiksha',       icon: '🎓', tone: 'bg-indigo-500/15 text-indigo-700 dark:text-indigo-300' },
  onedrive:      { label: 'OneDrive',      icon: '📁', tone: 'bg-sky-500/15 text-sky-700 dark:text-sky-300' },
  'ux-agent':    { label: 'UX',            icon: '🎨', tone: 'bg-pink-500/15 text-pink-700 dark:text-pink-300' },
}

function AgentBadge({ agentId }) {
  const meta = AGENT_META[agentId] || { label: agentId, icon: '🤖', tone: 'bg-beige-200 text-ink-soft dark:bg-gray-700 dark:text-white-soft' }
  return (
    <div className={`inline-flex items-center gap-1 text-[10.5px] font-medium px-2 py-0.5 rounded-full mb-1 ${meta.tone}`}>
      <span>{meta.icon}</span>
      <span>{meta.label}</span>
    </div>
  )
}

// ── Rich inline cards rendered below Lumen messages ─────────

// Connect-GitHub prompt shown in chat when the portfolio agent is used before
// connecting. Opens GitHub's authorize popup; on success re-sends the request.
function ConnectGithubCard({ data, onSend }) {
  const [status, setStatus] = useState('idle') // idle | connecting | connected
  const [error, setError] = useState('')

  const connect = async () => {
    setStatus('connecting'); setError('')
    try {
      const r = await githubConnect()
      setStatus('connected')
      const retry = data?.retry_message
      if (retry && onSend) setTimeout(() => onSend(retry, { fromVoice: false }), 400)
    } catch (e) {
      setStatus('idle')
      setError(e?.message || 'GitHub connect failed')
    }
  }

  if (status === 'connected') {
    return (
      <div className="rounded-card bg-emerald-50 border border-emerald-200 p-3">
        <div className="text-[12px] text-emerald-800 font-medium">✅ GitHub connected — running your request…</div>
      </div>
    )
  }
  return (
    <div className="rounded-card bg-white dark:bg-gray-800 border border-beige-200 dark:border-gray-700 p-3 flex flex-col gap-2">
      <div className="text-[12px] font-medium text-ink dark:text-white flex items-center gap-1.5">
        <span>📁</span> Connect GitHub
      </div>
      <div className="text-2xs text-ink-muted dark:text-white-muted">
        Opens GitHub in a popup — click <b>Authorize</b> and you're done. No token to paste.
      </div>
      <button type="button" onClick={connect} disabled={status === 'connecting'}
        className="self-start text-[12px] px-3 py-1.5 rounded-pill bg-black text-white hover:bg-zinc-800 disabled:opacity-50">
        {status === 'connecting' ? 'Waiting for GitHub authorization…' : 'Connect GitHub'}
      </button>
      {error && <div className="text-[11px] text-rose-600">{error}</div>}
    </div>
  )
}

// Google consent prompt — "Allow once" (this request only) vs "Always allow"
// (persist). On approval, connects Google and re-sends the original request.
function ConnectGoogleCard({ data, onSend }) {
  const [status, setStatus] = useState('idle') // idle | connecting | connected
  const [error, setError] = useState('')
  const service = data?.service || 'Google'

  const connect = async (mode) => {
    setStatus('connecting'); setError('')
    try {
      await googleConnect(mode)
      setStatus('connected')
      const retry = data?.retry_message
      if (retry && onSend) setTimeout(() => onSend(retry, { fromVoice: false }), 400)
    } catch (e) {
      setStatus('idle')
      setError(e?.message || 'Google connect failed')
    }
  }

  if (status === 'connected') {
    return (
      <div className="rounded-card bg-emerald-50 border border-emerald-200 p-3">
        <div className="text-[12px] text-emerald-800 font-medium">✅ {service} connected — running your request…</div>
      </div>
    )
  }
  return (
    <div className="rounded-card bg-white dark:bg-gray-800 border border-beige-200 dark:border-gray-700 p-3 flex flex-col gap-2">
      <div className="text-[12px] font-medium text-ink dark:text-white flex items-center gap-1.5">
        <span>🔐</span> Allow access to your {service}?
      </div>
      <div className="text-2xs text-ink-muted dark:text-white-muted">
        <b>Allow once</b> grants access for this request only. <b>Always allow</b> keeps it connected so Lumen won't ask again.
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <button type="button" onClick={() => connect('once')} disabled={status === 'connecting'}
          className="text-[12px] px-3 py-1.5 rounded-pill bg-beige-100 border border-beige-300 text-ink hover:bg-beige-200 disabled:opacity-50">
          {status === 'connecting' ? 'Opening…' : 'Allow once'}
        </button>
        <button type="button" onClick={() => connect('always')} disabled={status === 'connecting'}
          className="text-[12px] px-3 py-1.5 rounded-pill bg-gray-800 text-white hover:bg-gray-700 disabled:opacity-50">
          Always allow
        </button>
      </div>
      {error && <div className="text-[11px] text-rose-600">{error}</div>}
    </div>
  )
}

// Confirmation card shown when the user asks to disconnect an integration in
// chat. Never disconnects on its own — only when the user taps Disconnect.
function ConfirmDisconnectCard({ data }) {
  const [status, setStatus] = useState('idle') // idle | working | done | cancelled
  const [error, setError] = useState('')
  const service = data?.service
  const label = data?.label || service

  const doDisconnect = async () => {
    setStatus('working'); setError('')
    try {
      if (service === 'github') await githubDisconnect()
      else if (service === 'notion') await notionDisconnect()
      else if (service === 'google') await googleDisconnect()
      else throw new Error('Unknown connection')
      setStatus('done')
    } catch (e) {
      setStatus('idle')
      setError(e?.message || 'Disconnect failed')
    }
  }

  if (status === 'done') {
    return (
      <div className="rounded-card bg-emerald-50 border border-emerald-200 p-3">
        <div className="text-[12px] text-emerald-800 font-medium">✅ {label} disconnected. Reconnect anytime in your Profile.</div>
      </div>
    )
  }
  if (status === 'cancelled') {
    return (
      <div className="rounded-card bg-white dark:bg-gray-800 border border-beige-200 dark:border-gray-700 p-3">
        <div className="text-[12px] text-ink-muted">Cancelled — {label} is still connected.</div>
      </div>
    )
  }
  return (
    <div className="rounded-card bg-white dark:bg-gray-800 border border-beige-200 dark:border-gray-700 p-3 flex flex-col gap-2">
      <div className="text-[12px] font-medium text-ink dark:text-white flex items-center gap-1.5">
        <span>⚠️</span> Disconnect {label}?
      </div>
      <div className="text-2xs text-ink-muted dark:text-white-muted">
        Lumen will lose access until you reconnect it in your Profile.
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <button type="button" onClick={doDisconnect} disabled={status === 'working'}
          className="text-[12px] px-3 py-1.5 rounded-pill bg-rose-600 text-white hover:bg-rose-700 disabled:opacity-50">
          {status === 'working' ? 'Disconnecting…' : 'Disconnect'}
        </button>
        <button type="button" onClick={() => setStatus('cancelled')} disabled={status === 'working'}
          className="text-[12px] px-3 py-1.5 rounded-pill bg-beige-100 border border-beige-300 text-ink hover:bg-beige-200 disabled:opacity-50">
          Cancel
        </button>
      </div>
      {error && <div className="text-[11px] text-rose-600">{error}</div>}
    </div>
  )
}

function InlineCard({ card, onSend }) {
  if (!card) return null
  switch (card.type) {
    case 'progress':         return <ProgressCard data={card.data} />
    case 'events':           return <EventsCard data={card.data} />
    case 'peers':            return <PeersCard data={card.data} />
    case 'email_sent':       return <EmailSentCard data={card.data} />
    case 'email_draft':      return <EmailDraftCard data={card.data} />
    case 'inbox':            return <InboxCard data={card.data} />
    case 'outlook_search':   return <OutlookSearchCard data={card.data} />
    case 'connect_github':   return <ConnectGithubCard data={card.data} onSend={onSend} />
    case 'connect_google':   return <ConnectGoogleCard data={card.data} onSend={onSend} />
    case 'confirm_disconnect': return <ConfirmDisconnectCard data={card.data} />
    // 'connect_email' card removed — IMAP no longer supported
    case 'portfolio_files':  return <PortfolioFilesCard data={card.data} />
    case 'shiksha_agents':   return <ShikshaAgentsCard data={card.data} />
    case 'shiksha_progress': return <ShikshaProgressCard data={card.data} />
    case 'notion_pages':     return <NotionPagesCard data={card.data} />
    case 'drive_files':      return <DriveFilesCard data={card.data} />
    case 'gmail_inbox':      return <GmailInboxCard data={card.data} />
    case 'gcal_events':      return <GcalEventsCard data={card.data} />
    default:                 return null
  }
}

function GcalEventsCard({ data }) {
  const events = Array.isArray(data) ? data : []
  if (events.length === 0) return null
  const fmtWhen = (e) => {
    const s = (e.start || '').replace('T', ' ').slice(0, 16)
    const en = (e.end || '').replace('T', ' ').slice(0, 16)
    if (e.all_day) return `${(e.start || '').slice(0, 10)} (all day)`
    if (s && en && s.slice(0, 10) === en.slice(0, 10)) return `${s} → ${en.slice(11)}`
    return s
  }
  return (
    <div className="rounded-card bg-white dark:bg-gray-800 border border-beige-200 dark:border-gray-700 p-3 space-y-2">
      <div className="text-[12px] font-medium text-ink dark:text-white">📅 Google Calendar</div>
      {events.map((e, i) => (
        <div key={e.id || i} className="rounded-inner border border-beige-200 dark:border-gray-700 p-2">
          <div className="text-[12.5px] font-medium text-ink dark:text-white">{e.title || '(no title)'}</div>
          <div className="text-2xs text-ink-muted dark:text-white-muted">{fmtWhen(e)}</div>
          {e.location && (
            <div className="text-2xs text-ink-muted dark:text-white-muted">📍 {e.location}</div>
          )}
          {e.attendees && e.attendees.length > 0 && (
            <div className="text-2xs text-ink-muted dark:text-white-muted">
              👥 {e.attendees.slice(0, 3).join(', ')}{e.attendees.length > 3 ? ` +${e.attendees.length - 3}` : ''}
            </div>
          )}
          {e.url && (
            <a href={e.url} target="_blank" rel="noreferrer"
              className="inline-block mt-1 text-[11px] px-2 py-1 rounded bg-beige-100 dark:bg-gray-700 text-ink dark:text-white hover:bg-beige-200">
              Open in Google Calendar ↗
            </a>
          )}
        </div>
      ))}
    </div>
  )
}

function DriveFilesCard({ data }) {
  const files = Array.isArray(data) ? data : []
  const [actionState, setActionState] = useState({})
  // Edit-state per row idx
  const [openEditor, setOpenEditor] = useState(null)
  const [editMode, setEditMode] = useState('append') // 'append' | 'replace' | 'find_replace'
  const [draft, setDraft] = useState('')
  const [findText, setFindText] = useState('')
  const [replaceText, setReplaceText] = useState('')
  const [editorBusy, setEditorBusy] = useState(false)
  const [editStatus, setEditStatus] = useState({})
  const [contentCache, setContentCache] = useState({}) // {idx: existing_text}

  const callBackend = async (path, payload) => {
    const token = localStorage.getItem('lumen.token')
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    return r.json()
  }

  const summarize = async (idx, f) => {
    setActionState(s => ({ ...s, [idx]: { status: 'summarizing', text: '' } }))
    try {
      const out = await callBackend('/lumen/drive/summarize', {
        file_id: f.id,
        instruction: 'Summarize this file in 2-3 sentences for a student.',
      })
      setActionState(s => ({ ...s, [idx]: { status: 'done', text: out.summary || out.error || '(no result)' } }))
    } catch (e) {
      setActionState(s => ({ ...s, [idx]: { status: 'error', text: e.message } }))
    }
  }

  const openEdit = async (idx, f) => {
    if (openEditor === idx) { setOpenEditor(null); setDraft(''); return }
    setOpenEditor(idx)
    setEditMode('append')
    setDraft('')
    setFindText(''); setReplaceText('')
    setEditorBusy(true)
    try {
      const out = await callBackend('/lumen/drive/read', { file_id: f.id })
      setContentCache(c => ({ ...c, [idx]: out?.content || '' }))
    } catch {
      setContentCache(c => ({ ...c, [idx]: '' }))
    } finally {
      setEditorBusy(false)
    }
  }

  const handleModeChange = (idx, mode) => {
    setEditMode(mode)
    if (mode === 'replace') {
      setDraft(contentCache[idx] || '')
    } else if (mode === 'append') {
      setDraft('')
    }
  }

  const commitEdit = async (idx, f) => {
    setEditStatus(s => ({ ...s, [idx]: 'saving' }))
    try {
      let out
      if (editMode === 'find_replace') {
        if (!findText) {
          setEditStatus(s => ({ ...s, [idx]: 'Enter the search text.' }))
          return
        }
        out = await callBackend('/lumen/drive/doc/find-replace', {
          file_id: f.id, find: findText, replace: replaceText,
        })
        if (out?.ok) {
          setEditStatus(s => ({ ...s, [idx]: `replaced (${out.occurrences})` }))
          setOpenEditor(null)
          return
        }
      } else if (editMode === 'replace') {
        if (!window.confirm(
          `Replace the ENTIRE content of "${f.name || 'this doc'}"?\n\n` +
          `This deletes existing content. Formatting (tables, images) will be lost.`
        )) {
          setEditStatus(s => ({ ...s, [idx]: undefined }))
          return
        }
        out = await callBackend('/lumen/drive/doc/replace', { file_id: f.id, content: draft })
        if (out?.ok) {
          setEditStatus(s => ({ ...s, [idx]: 'replaced' }))
          setOpenEditor(null)
          return
        }
      } else {
        // append
        if (!draft.trim()) {
          setEditStatus(s => ({ ...s, [idx]: 'Enter content to append.' }))
          return
        }
        out = await callBackend('/lumen/drive/doc/append', { file_id: f.id, content: draft })
        if (out?.ok) {
          setEditStatus(s => ({ ...s, [idx]: 'appended' }))
          setOpenEditor(null)
          return
        }
      }
      setEditStatus(s => ({ ...s, [idx]: out?.error || 'Edit failed' }))
    } catch (e) {
      setEditStatus(s => ({ ...s, [idx]: e.message }))
    }
  }

  if (files.length === 0) return null
  return (
    <div className="rounded-card bg-white dark:bg-gray-800 border border-beige-200 dark:border-gray-700 p-3 space-y-2">
      <div className="text-[12px] font-medium text-ink dark:text-white">📁 Drive files</div>
      {files.map((f, i) => {
        const st = actionState[i] || {}
        const es = editStatus[i]
        const isGoogleDoc = (f.mime_type || '').includes('vnd.google-apps.document')
        const niceType = (f.mime_type || '').includes('document') ? 'Doc'
                       : (f.mime_type || '').includes('spreadsheet') ? 'Sheet'
                       : (f.mime_type || '').includes('pdf') ? 'PDF'
                       : (f.mime_type || '').includes('presentation') ? 'Slides'
                       : 'File'
        return (
          <div key={f.id || i} className="rounded-inner border border-beige-200 dark:border-gray-700 p-2">
            <div className="text-[12.5px] font-medium text-ink dark:text-white">
              {f.name || 'Untitled'} <span className="text-2xs text-ink-muted dark:text-white-muted">· {niceType}</span>
            </div>
            {f.modified_time && (
              <div className="text-2xs text-ink-muted dark:text-white-muted">
                edited {new Date(f.modified_time).toLocaleDateString()}
              </div>
            )}
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {f.url && (
                <a href={f.url} target="_blank" rel="noreferrer"
                  className="text-[11px] px-2 py-1 rounded bg-beige-100 dark:bg-gray-700 text-ink dark:text-white hover:bg-beige-200">
                  Open in Drive ↗
                </a>
              )}
              <button onClick={() => summarize(i, f)}
                disabled={st.status === 'summarizing'}
                className="text-[11px] px-2 py-1 rounded bg-amber text-white hover:bg-amber/90 disabled:opacity-50">
                {st.status === 'summarizing' ? 'Summarizing…' : '📝 Summarize'}
              </button>
              {isGoogleDoc && (
                <button onClick={() => openEdit(i, f)}
                  className="text-[11px] px-2 py-1 rounded bg-blue-600 text-white hover:bg-blue-700">
                  ✏️ Edit
                </button>
              )}
            </div>
            {openEditor === i && (
              <div className="mt-2 space-y-1.5">
                <div className="flex gap-1.5 items-center text-[11px] flex-wrap">
                  <button type="button" onClick={() => handleModeChange(i, 'append')}
                    className={`px-2 py-0.5 rounded ${editMode === 'append'
                      ? 'bg-emerald-600 text-white'
                      : 'bg-beige-100 dark:bg-gray-700 text-ink dark:text-white-soft'}`}>
                    Append
                  </button>
                  <button type="button" onClick={() => handleModeChange(i, 'replace')}
                    className={`px-2 py-0.5 rounded ${editMode === 'replace'
                      ? 'bg-rose-600 text-white'
                      : 'bg-beige-100 dark:bg-gray-700 text-ink dark:text-white-soft'}`}>
                    Replace whole doc
                  </button>
                  <button type="button" onClick={() => handleModeChange(i, 'find_replace')}
                    className={`px-2 py-0.5 rounded ${editMode === 'find_replace'
                      ? 'bg-blue-600 text-white'
                      : 'bg-beige-100 dark:bg-gray-700 text-ink dark:text-white-soft'}`}>
                    Find &amp; Replace
                  </button>
                  {editorBusy && <span className="text-ink-muted dark:text-white-muted">loading…</span>}
                </div>
                {editMode === 'replace' && (
                  <div className="text-2xs text-rose-700 dark:text-rose-400">
                    ⚠ Replace wipes the entire body. Formatting / tables / images are lost.
                  </div>
                )}
                {editMode === 'find_replace' ? (
                  <div className="space-y-1">
                    <input value={findText} onChange={e => setFindText(e.target.value)}
                      placeholder="Find this text…"
                      className="w-full px-2 py-1.5 text-[12px] bg-beige-50 dark:bg-gray-900 border border-beige-200 dark:border-gray-700 rounded text-ink dark:text-white" />
                    <input value={replaceText} onChange={e => setReplaceText(e.target.value)}
                      placeholder="Replace with…"
                      className="w-full px-2 py-1.5 text-[12px] bg-beige-50 dark:bg-gray-900 border border-beige-200 dark:border-gray-700 rounded text-ink dark:text-white" />
                  </div>
                ) : (
                  <textarea value={draft} onChange={e => setDraft(e.target.value)}
                    rows={editMode === 'replace' ? 10 : 4}
                    placeholder={editMode === 'replace'
                      ? 'Edit the full content here. Empty = wipe everything.'
                      : 'Text to append at the end of the doc'}
                    className="w-full px-2 py-1.5 text-[12px] bg-beige-50 dark:bg-gray-900 border border-beige-200 dark:border-gray-700 rounded text-ink dark:text-white font-mono" />
                )}
                <div className="flex gap-1.5 items-center">
                  <button onClick={() => commitEdit(i, f)}
                    disabled={es === 'saving'}
                    className={`text-[11px] px-2 py-1 rounded text-white disabled:opacity-50
                      ${editMode === 'replace' ? 'bg-rose-600 hover:bg-rose-700'
                       : editMode === 'find_replace' ? 'bg-blue-600 hover:bg-blue-700'
                       : 'bg-emerald-600 hover:bg-emerald-700'}`}>
                    {es === 'saving' ? 'Saving…'
                     : editMode === 'replace' ? 'Replace'
                     : editMode === 'find_replace' ? 'Find & Replace'
                     : 'Append'}
                  </button>
                  <button onClick={() => { setOpenEditor(null); setDraft(''); }}
                    className="text-[11px] px-2 py-1 rounded bg-beige-100 dark:bg-gray-700 text-ink dark:text-white hover:bg-beige-200">
                    Cancel
                  </button>
                  {es === 'appended' && <span className="text-[11px] text-emerald-700">✓ Appended</span>}
                  {es === 'replaced' && <span className="text-[11px] text-emerald-700">✓ Replaced</span>}
                  {es && typeof es === 'string' && es.startsWith('replaced (') &&
                    <span className="text-[11px] text-emerald-700">✓ {es}</span>}
                  {es && es !== 'saving' && es !== 'appended' && es !== 'replaced' && !es.startsWith?.('replaced (') && (
                    <span className="text-[11px] text-rose-700">{es}</span>
                  )}
                </div>
              </div>
            )}
            {st.status === 'done' && (
              <div className="mt-2 p-2 bg-beige-50 dark:bg-gray-900 rounded text-[11.5px] text-ink dark:text-white-soft whitespace-pre-wrap">
                {st.text}
              </div>
            )}
            {st.status === 'error' && (
              <div className="mt-2 text-[11px] text-rose-700 bg-rose-50 p-1.5 rounded">{st.text}</div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function GmailInboxCard({ data }) {
  const messages = Array.isArray(data) ? data : []
  const [actionState, setActionState] = useState({})

  const callBackend = async (path, payload) => {
    const token = localStorage.getItem('lumen.token')
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    return r.json()
  }

  const summarize = async (idx, m) => {
    setActionState(s => ({ ...s, [idx]: { status: 'summarizing', text: '' } }))
    try {
      const out = await callBackend('/lumen/gmail/summarize', {
        message_id: m.id,
        instruction: 'Summarize this email in 2-3 short sentences. Note any deadlines/asks.',
      })
      setActionState(s => ({ ...s, [idx]: { status: 'done', text: out.summary || '(no result)' } }))
    } catch (e) {
      setActionState(s => ({ ...s, [idx]: { status: 'error', text: e.message } }))
    }
  }

  if (messages.length === 0) return null
  return (
    <div className="rounded-card bg-white dark:bg-gray-800 border border-beige-200 dark:border-gray-700 p-3 space-y-2">
      <div className="text-[12px] font-medium text-ink dark:text-white">📧 Gmail</div>
      {messages.map((m, i) => {
        const st = actionState[i] || {}
        return (
          <div key={m.id || i} className="rounded-inner border border-beige-200 dark:border-gray-700 p-2">
            <div className="text-[12.5px] font-medium text-ink dark:text-white">
              {m.subject || '(no subject)'}
            </div>
            <div className="text-2xs text-ink-muted dark:text-white-muted">
              from {m.sender || '?'}{m.senderEmail ? ` <${m.senderEmail}>` : ''}
            </div>
            {m.snippet && (
              <div className="text-[11.5px] text-ink-soft dark:text-white-soft mt-1 line-clamp-2">
                {m.snippet}
              </div>
            )}
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              <button onClick={() => summarize(i, m)}
                disabled={st.status === 'summarizing'}
                className="text-[11px] px-2 py-1 rounded bg-amber text-white hover:bg-amber/90 disabled:opacity-50">
                {st.status === 'summarizing' ? 'Summarizing…' : '📝 Summarize'}
              </button>
            </div>
            {st.status === 'done' && (
              <div className="mt-2 p-2 bg-beige-50 dark:bg-gray-900 rounded text-[11.5px] text-ink dark:text-white-soft whitespace-pre-wrap">
                {st.text}
              </div>
            )}
            {st.status === 'error' && (
              <div className="mt-2 text-[11px] text-rose-700 bg-rose-50 p-1.5 rounded">{st.text}</div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function NotionPagesCard({ data }) {
  // Back-compat: data is either an array of pages, or { pages, edit_mode }.
  const pages = Array.isArray(data) ? data : (data?.pages || [])
  const editMode = !Array.isArray(data) && !!data?.edit_mode
  const [actionState, setActionState] = useState({}) // { idx: { status, text } }
  const [openEditor, setOpenEditor] = useState(null) // page idx whose editor is open
  const [draft, setDraft] = useState('')
  const [editMode2, setEditMode2] = useState('append') // 'append' | 'replace'
  const [editorBusy, setEditorBusy] = useState(false)  // loading existing content
  const [appendState, setAppendState] = useState({}) // { idx: 'saving' | 'done' | error string }

  const callBackend = async (path, payload) => {
    const token = localStorage.getItem('lumen.token')
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    return r.json()
  }

  const summarize = async (idx, page) => {
    setActionState(s => ({ ...s, [idx]: { status: 'summarizing', text: '' } }))
    try {
      const out = await callBackend('/lumen/notion/summarize', {
        page_id: page.id,
        instruction: 'Summarize this page in 2-3 sentences for a student.',
      })
      setActionState(s => ({ ...s, [idx]: { status: 'done', text: out.summary || out.error || '(no result)' } }))
    } catch (e) {
      setActionState(s => ({ ...s, [idx]: { status: 'error', text: e.message } }))
    }
  }

  // Open Edit panel: pre-fetch current page content so the user can edit-in-place
  const openEdit = async (idx, page) => {
    if (openEditor === idx) {
      setOpenEditor(null)
      setDraft('')
      return
    }
    setOpenEditor(idx)
    setEditMode2('append')
    setEditorBusy(true)
    setDraft('')
    try {
      const out = await callBackend('/lumen/notion/read', { page_id: page.id })
      const md = (out?.content_md || '').trim()
      // Pre-fill only when Replace mode is selected — but stash it for that toggle
      setDraft('')  // start empty for Append mode
      setEditorContent(idx, md)
    } catch (e) {
      setEditorContent(idx, '')
    } finally {
      setEditorBusy(false)
    }
  }

  // Per-page current content cache (for Replace mode prefill)
  const [contentCache, setContentCache] = useState({}) // { idx: existing_markdown }
  const setEditorContent = (idx, content) => {
    setContentCache(c => ({ ...c, [idx]: content }))
  }

  const handleModeChange = (idx, mode) => {
    setEditMode2(mode)
    if (mode === 'replace') {
      // Prefill the textarea with the page's existing content so user can edit in place
      setDraft(contentCache[idx] || '')
    } else {
      // Append mode — start empty
      setDraft('')
    }
  }

  const append = async (idx, page) => {
    const lines = draft.split('\n').map(l => l.trim()).filter(Boolean)
    if (lines.length === 0) {
      setAppendState(s => ({ ...s, [idx]: 'Enter at least one line.' }))
      return
    }
    if (editMode2 === 'replace') {
      const ok = window.confirm(
        `Replace the ENTIRE content of "${page.title || 'this page'}"?\n\n` +
        `This deletes every existing block. Rich formatting (tables, embeds, sub-pages, colors) will be lost.`
      )
      if (!ok) return
    }
    setAppendState(s => ({ ...s, [idx]: 'saving' }))
    try {
      const endpoint = editMode2 === 'replace' ? '/lumen/notion/replace' : '/lumen/notion/append'
      const out = await callBackend(endpoint, { page_id: page.id, lines })
      if (out?.ok) {
        setAppendState(s => ({ ...s, [idx]: editMode2 === 'replace' ? 'replaced' : 'done' }))
        setOpenEditor(null)
        setDraft('')
      } else {
        setAppendState(s => ({ ...s, [idx]: out?.error || (editMode2 === 'replace' ? 'Replace failed' : 'Append failed') }))
      }
    } catch (e) {
      setAppendState(s => ({ ...s, [idx]: e.message }))
    }
  }

  if (pages.length === 0) return null
  return (
    <div className="rounded-card bg-white dark:bg-gray-800 border border-beige-200 dark:border-gray-700 p-3 space-y-2">
      <div className="text-[12px] font-medium text-ink dark:text-white">
        {editMode ? '✏️ Pick a Notion page to edit' : '📓 Notion pages'}
      </div>
      {pages.map((p, i) => {
        const st = actionState[i] || {}
        const ap = appendState[i]
        return (
          <div key={p.id || i} className="rounded-inner border border-beige-200 dark:border-gray-700 p-2">
            <div className="text-[12.5px] font-medium text-ink dark:text-white">
              {p.title || 'Untitled'}
            </div>
            {p.last_edited && (
              <div className="text-2xs text-ink-muted dark:text-white-muted">
                edited {new Date(p.last_edited).toLocaleDateString()}
              </div>
            )}
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {p.url && (
                <a href={p.url} target="_blank" rel="noreferrer"
                  className="text-[11px] px-2 py-1 rounded bg-beige-100 dark:bg-gray-700 text-ink dark:text-white hover:bg-beige-200">
                  Open in Notion ↗
                </a>
              )}
              <button onClick={() => summarize(i, p)}
                disabled={st.status === 'summarizing'}
                className="text-[11px] px-2 py-1 rounded bg-amber text-white hover:bg-amber/90 disabled:opacity-50">
                {st.status === 'summarizing' ? 'Summarizing…' : '📝 Summarize'}
              </button>
              {editMode && (
                <button onClick={() => openEdit(i, p)}
                  className="text-[11px] px-2 py-1 rounded bg-blue-600 text-white hover:bg-blue-700">
                  ✏️ Edit
                </button>
              )}
            </div>
            {openEditor === i && (
              <div className="mt-2 space-y-1.5">
                {/* Mode toggle: Append (safe, default) vs Replace (destructive) */}
                <div className="flex gap-1.5 items-center text-[11px]">
                  <button type="button" onClick={() => handleModeChange(i, 'append')}
                    className={`px-2 py-0.5 rounded ${editMode2 === 'append'
                      ? 'bg-emerald-600 text-white'
                      : 'bg-beige-100 dark:bg-gray-700 text-ink dark:text-white-soft'}`}>
                    Append
                  </button>
                  <button type="button" onClick={() => handleModeChange(i, 'replace')}
                    className={`px-2 py-0.5 rounded ${editMode2 === 'replace'
                      ? 'bg-rose-600 text-white'
                      : 'bg-beige-100 dark:bg-gray-700 text-ink dark:text-white-soft'}`}>
                    Replace whole page
                  </button>
                  {editorBusy && <span className="text-ink-muted dark:text-white-muted">loading…</span>}
                </div>
                {editMode2 === 'replace' && contentCache[i] !== undefined && !editorBusy && (
                  <div className="text-2xs text-rose-700 dark:text-rose-400">
                    ⚠ Replace deletes all existing blocks. Formatting (tables, embeds, sub-pages) will be lost.
                  </div>
                )}
                <textarea value={draft} onChange={e => setDraft(e.target.value)}
                  rows={editMode2 === 'replace' ? 10 : 4}
                  placeholder={editMode2 === 'replace'
                    ? 'Edit the full content — one line per block. Empty lines are skipped.'
                    : 'Type lines to append — one per line'}
                  className="w-full px-2 py-1.5 text-[12px] bg-beige-50 dark:bg-gray-900 border border-beige-200 dark:border-gray-700 rounded text-ink dark:text-white font-mono" />
                <div className="flex gap-1.5 items-center">
                  <button onClick={() => append(i, p)}
                    disabled={ap === 'saving'}
                    className={`text-[11px] px-2 py-1 rounded text-white disabled:opacity-50
                      ${editMode2 === 'replace' ? 'bg-rose-600 hover:bg-rose-700' : 'bg-emerald-600 hover:bg-emerald-700'}`}>
                    {ap === 'saving'
                      ? (editMode2 === 'replace' ? 'Replacing…' : 'Appending…')
                      : (editMode2 === 'replace' ? 'Replace' : 'Append')}
                  </button>
                  <button onClick={() => { setOpenEditor(null); setDraft(''); }}
                    className="text-[11px] px-2 py-1 rounded bg-beige-100 dark:bg-gray-700 text-ink dark:text-white hover:bg-beige-200">
                    Cancel
                  </button>
                  {ap === 'done' && <span className="text-[11px] text-emerald-700">✓ Appended</span>}
                  {ap === 'replaced' && <span className="text-[11px] text-emerald-700">✓ Replaced</span>}
                  {ap && ap !== 'saving' && ap !== 'done' && ap !== 'replaced' && (
                    <span className="text-[11px] text-rose-700">{ap}</span>
                  )}
                </div>
              </div>
            )}
            {st.status === 'done' && (
              <div className="mt-2 p-2 bg-beige-50 dark:bg-gray-900 rounded text-[11.5px] text-ink dark:text-white-soft whitespace-pre-wrap">
                {st.text}
              </div>
            )}
            {st.status === 'error' && (
              <div className="mt-2 text-[11px] text-rose-700 bg-rose-50 p-1.5 rounded">{st.text}</div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function ProgressCard({ data }) {
  if (!data) return null
  const items = Array.isArray(data) ? data : [data]
  return (
    <div className="flex flex-col gap-2">
      {items.map((d, i) => {
        const pct = d.pct || 0
        return (
          <div key={i} className="rounded-card bg-white dark:bg-gray-800 border border-beige-200 dark:border-gray-700 p-3">
            <div className="flex items-center justify-between">
              <div className="text-[13px] font-medium text-ink dark:text-white">{d.ta_name || d.ta_id || 'Agent'}</div>
              <span className="text-2xs text-ink dark:text-white-muted capitalize">L{d.level || 1} \u00b7 {d.label || 'beginner'}</span>
            </div>
            <div className="mt-2">
              <div className="flex justify-between text-2xs text-ink dark:text-white-muted mb-1">
                <span>{d.module || ''}</span>
                <span>{pct}%</span>
              </div>
              <div className="h-[5px] rounded-full bg-beige-200 overflow-hidden">
                <div className="h-full bg-amber transition-all" style={{ width: `${pct}%` }} />
              </div>
            </div>
            <div className="mt-2 flex gap-3 text-2xs text-ink dark:text-white-muted">
              <span>{d.sessions || 0} sessions</span>
              <span>{(d.topics_mastered || []).length || d.mastered_count || 0} mastered</span>
              <span>{(d.topics_covered || []).length || d.covered_count || 0} covered</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}

function EventsCard({ data }) {
  if (!data || !Array.isArray(data) || data.length === 0) return null
  return (
    <div className="rounded-card bg-white dark:bg-gray-800 border border-beige-200 dark:border-gray-700 p-3">
      <div className="text-[12px] font-medium text-ink dark:text-white mb-2">\ud83d\udcc5 Upcoming Events</div>
      <div className="flex flex-col gap-1.5">
        {data.slice(0, 6).map((ev, i) => (
          <div key={i} className="flex items-center gap-2 text-[12px]">
            <span className={`w-2 h-2 rounded-full shrink-0 ${
              ev.status === 'completed' ? 'bg-emerald-400' : 'bg-amber'
            }`} />
            <span className="text-ink dark:text-white flex-1 truncate">{ev.title}</span>
            <span className="text-ink dark:text-white-muted text-2xs shrink-0">
              {ev.date}{ev.time ? ` ${ev.time}` : ''}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

function PeersCard({ data }) {
  if (!data || !Array.isArray(data) || data.length === 0) return null
  return (
    <div className="rounded-card bg-white dark:bg-gray-800 border border-beige-200 dark:border-gray-700 p-3">
      <div className="text-[12px] font-medium text-ink dark:text-white mb-2">\ud83d\udc65 Peers on your network</div>
      <div className="flex flex-col gap-1.5">
        {data.slice(0, 5).map((p, i) => {
          const initials = (p.name || 'P').split(/[\s@._-]+/).filter(Boolean).slice(0, 2).map(s => s[0]?.toUpperCase()).join('')
          return (
            <div key={i} className="flex items-center gap-2 text-[12px]">
              <div className="w-6 h-6 rounded-full bg-amber text-white text-[9px] font-medium flex items-center justify-center shrink-0">
                {initials}
              </div>
              <span className="text-ink dark:text-white flex-1 truncate">{p.name || 'Peer'}</span>
              <span className="text-ink dark:text-white-muted text-2xs shrink-0">
                {p.sessions || p.total_sessions || 0}s \u00b7 {p.tcs_mastered || 0}tc
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function EmailDraftCard({ data }) {
  const [state, setState] = useState({
    to: data?.to || '',
    to_email: data?.to_email || '',
    subject: data?.subject || '',
    body: data?.body || '',
  })
  const [editing, setEditing] = useState(false)
  const [status, setStatus] = useState('draft') // draft | sending | sent | failed | cancelled
  const [error, setError] = useState('')
  const [resolveStatus, setResolveStatus] = useState('idle') // idle | looking | found | not_found
  // Check whether the user has IMAP email connected — drives the send strategy
  const [emailConfig, setEmailConfig] = useState({ connected: false, blocked: false })
  // Check whether the user has Google (Gmail) connected — top-priority send strategy
  const [gmailConnected, setGmailConnected] = useState(false)

  useEffect(() => {
    const token = localStorage.getItem('lumen.token')
    if (!token) return
    fetch('/lumen/email/connection-status', { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.ok ? r.json() : null)
      .then(r => { if (r) setEmailConfig({ connected: !!r.connected, blocked: !!r.blocked, email: r.email || '' }) })
      .catch(() => {})
    fetch('/lumen/google/status', { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.ok ? r.json() : null)
      .then(r => { if (r) setGmailConnected(!!r.gmail) })
      .catch(() => {})
  }, [])

  // Backend marks the draft with auto_send=true when the user has already
  // confirmed (e.g. typed "yes send this"). Auto-fire handleSend so the user
  // doesn't have to click Send a second time.
  const autoSendRef = useRef(false)
  useEffect(() => {
    if (data?.auto_send && !autoSendRef.current && state.to_email) {
      autoSendRef.current = true
      // Defer to next tick so all state initialisers settle
      setTimeout(() => handleSend(), 100)
    }
  }, [data?.auto_send, state.to_email])

  // Auto-resolve recipient email from Outlook if backend gave us only a name
  useEffect(() => {
    // Skip if we already have an email, or no name to look up, or "to" is already an email
    const name = (data?.to || '').trim()
    const existingEmail = (data?.to_email || '').trim()
    if (existingEmail) return
    if (!name) return
    if (/^[\w.+-]+@[\w-]+\.[\w.-]+$/.test(name)) return  // already an email
    if (/^(me|myself|self)$/i.test(name)) return

    // Wait for extension bridge
    const waitForExt = () => new Promise((resolve) => {
      if (window.lumenExt?.isInstalled) return resolve(true)
      let elapsed = 0
      const tick = setInterval(() => {
        if (window.lumenExt?.isInstalled) { clearInterval(tick); resolve(true) }
        elapsed += 100
        if (elapsed >= 2000) { clearInterval(tick); resolve(false) }
      }, 100)
    })

    // Get the user's own email so we never resolve a name to ourselves
    const myEmail = (() => {
      try { return (JSON.parse(localStorage.getItem('lumen.user') || '{}').email || '').toLowerCase() } catch { return '' }
    })()

    setResolveStatus('looking')
    waitForExt().then(ready => {
      if (!ready) { setResolveStatus('idle'); return }
      // Use "from:" prefix so Outlook filters by sender, not body content
      return window.lumenExt.searchInbox(`from:${name}`).then(r => {
        if (!r?.ok || !Array.isArray(r.results)) {
          setResolveStatus('not_found')
          return
        }
        const tokens = name.toLowerCase().split(/\s+/).filter(t => t.length > 1)
        let best = null
        for (const row of r.results) {
          const senderLower = (row.sender || '').toLowerCase()
          const senderEmail = (row.senderEmail || '').toLowerCase()
          if (!senderEmail) continue
          if (senderEmail === myEmail) continue  // never resolve to ourselves
          if (tokens.some(t => senderLower.includes(t) || senderEmail.includes(t))) {
            best = row
            break
          }
        }
        // No name-match fallback — silently picking a random sender was worse than asking
        if (best?.senderEmail) {
          setState(s => ({ ...s, to_email: best.senderEmail, to: best.sender || s.to }))
          setResolveStatus('found')
        } else {
          setResolveStatus('not_found')
        }
      }).catch(() => setResolveStatus('not_found'))
    })
  }, [data?.to, data?.to_email])

  if (!data) return null

  // User-info shortcuts
  const userInfo = (() => {
    try { return JSON.parse(localStorage.getItem('lumen.user') || '{}') } catch { return {} }
  })()
  const isEntraUser = userInfo?.id && !userInfo.id.startsWith('google-') && !userInfo.id.startsWith('email-') && !userInfo.id.startsWith('ext-')
  const isGoogleUser = userInfo?.id?.startsWith('google-')

  const handleSend = async () => {
    if (!state.to_email) {
      setError('Recipient email is required. Click Edit to add one.')
      return
    }
    setStatus('sending')
    setError('')

    try {
      // 0. TOP PRIORITY: Gmail API if Google is connected (works without any extension).
      if (gmailConnected) {
        const token = localStorage.getItem('lumen.token')
        const r = await fetch('/lumen/gmail/send', {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ to: state.to_email, subject: state.subject, body: state.body }),
        })
        const result = await r.json()
        if (result?.status === 'sent') { setStatus('sent'); return }
        setStatus('failed')
        setError(result?.error || 'Gmail send failed.')
        return
      }

      // (IMAP path removed — Lumen uses Gmail API for Google users and the
      // Chrome extension for Outlook users.)

      // 2. EXTENSION PATH: if the Chrome extension is installed, use it.
      // Works for any account type (Entra, email-pw, Google) since the extension
      // operates inside the user's Outlook tab — no Graph permissions needed.
      if (window.lumenExt?.isInstalled) {
        const r = await window.lumenExt.composeAndSend({
          to: state.to_email,
          subject: state.subject,
          body: state.body,
          send: true,
        })
        if (r?.sent) {
          // Log to outbox so "what did I send today?" works
          const token = localStorage.getItem('lumen.token')
          fetch('/lumen/comm/extension/log-sent', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ to: state.to_email, subject: state.subject, body: state.body }),
          }).catch(() => {})
          setStatus('sent')
          return
        }
        setStatus('failed')
        setError(r?.error || 'Extension could not send — check that Outlook tab is open.')
        return
      }

      // 3. Google user without IMAP and no extension → Gmail token + Gmail API
      if (isGoogleUser) {
        const result = await api.commSendReal({ ...data, ...state }, null, 'google')
        if (result?.status === 'sent') { setStatus('sent'); return }
        setStatus('failed')
        setError(result?.error || 'Gmail send failed. Try saying "connect my email" in chat.')
        return
      }

      // 4. No IMAP, no extension, no Google — guide user
      setStatus('failed')
      setError('To send, install the Lumen for Outlook Chrome extension (and open Outlook), or type "connect my email" for IMAP setup.')
    } catch (e) {
      setStatus('failed')
      setError(e?.message || 'Could not send')
    }
  }

  const handleMailto = () => {
    if (isGoogleUser) {
      // Open Gmail compose directly
      const gmailUrl = `https://mail.google.com/mail/?view=cm&to=${encodeURIComponent(state.to_email)}&su=${encodeURIComponent(state.subject)}&body=${encodeURIComponent(state.body)}`
      window.open(gmailUrl, '_blank')
    } else {
      const mailto = `mailto:${encodeURIComponent(state.to_email)}?subject=${encodeURIComponent(state.subject)}&body=${encodeURIComponent(state.body)}`
      window.open(mailto, '_blank')
    }
    setStatus('sent_via_mailto')
  }

  if (status === 'sent' || status === 'sent_via_mailto') {
    return (
      <div className="rounded-card bg-white dark:bg-gray-800 border border-beige-200 dark:border-gray-700 p-3">
        <div className="text-[12px] font-medium text-ink dark:text-white mb-2">
          {'\u2709\uFE0F'} {status === 'sent' ? 'Email Sent' : 'Opened in your email client'}
        </div>
        <div className="text-[12px] text-ink dark:text-white-muted space-y-1">
          <div><span className="text-ink dark:text-white font-medium">To:</span> {state.to} &lt;{state.to_email}&gt;</div>
          <div><span className="text-ink dark:text-white font-medium">Subject:</span> {state.subject}</div>
          <div className="mt-2 p-2 bg-beige-50 dark:bg-gray-900 rounded-inner text-[11.5px] text-ink dark:text-white-soft whitespace-pre-wrap">{state.body}</div>
        </div>
      </div>
    )
  }

  if (status === 'cancelled') {
    return (
      <div className="rounded-card bg-white dark:bg-gray-800 border border-beige-200 dark:border-gray-700 p-3 text-[12px] text-ink dark:text-white-muted italic">
        Draft discarded.
      </div>
    )
  }

  return (
    <div className="rounded-card bg-white dark:bg-gray-800 border border-beige-200 dark:border-gray-700 p-3">
      <div className="text-[12px] font-medium text-ink dark:text-white mb-2">{'\ud83d\udcdd'} Draft — review before sending</div>
      <div className="space-y-2">
        {editing ? (
          <>
            <label className="block text-[11px] text-ink dark:text-white-muted">To (name)
              <input value={state.to} onChange={e => setState(s => ({ ...s, to: e.target.value }))}
                className="mt-0.5 w-full px-2 py-1 text-[12px] bg-beige-50 dark:bg-gray-900 border border-beige-200 dark:border-gray-700 rounded" />
            </label>
            <label className="block text-[11px] text-ink dark:text-white-muted">To (email)
              <input value={state.to_email} onChange={e => setState(s => ({ ...s, to_email: e.target.value }))}
                placeholder="someone@microsoft.com"
                className="mt-0.5 w-full px-2 py-1 text-[12px] bg-beige-50 dark:bg-gray-900 border border-beige-200 dark:border-gray-700 rounded" />
            </label>
            <label className="block text-[11px] text-ink dark:text-white-muted">Subject
              <input value={state.subject} onChange={e => setState(s => ({ ...s, subject: e.target.value }))}
                className="mt-0.5 w-full px-2 py-1 text-[12px] bg-beige-50 dark:bg-gray-900 border border-beige-200 dark:border-gray-700 rounded" />
            </label>
            <label className="block text-[11px] text-ink dark:text-white-muted">Body
              <textarea value={state.body} onChange={e => setState(s => ({ ...s, body: e.target.value }))}
                rows={5}
                className="mt-0.5 w-full px-2 py-1 text-[12px] bg-beige-50 dark:bg-gray-900 border border-beige-200 dark:border-gray-700 rounded" />
            </label>
          </>
        ) : (
          <div className="text-[12px] text-ink dark:text-white-muted space-y-1">
            <div>
              <span className="text-ink dark:text-white font-medium">To:</span> {state.to}{' '}
              {state.to_email
                ? <span className="text-ink dark:text-white-soft">&lt;{state.to_email}&gt;</span>
                : resolveStatus === 'looking'
                  ? <span className="text-amber italic">looking up in Outlook…</span>
                  : resolveStatus === 'not_found'
                    ? <span className="text-amber italic">(no email found — edit to add)</span>
                    : <span className="text-amber italic">(no email — edit to add)</span>}
              {resolveStatus === 'found' && state.to_email && (
                <span className="ml-1 text-emerald-600 text-[11px]">✓ from Outlook</span>
              )}
            </div>
            <div><span className="text-ink dark:text-white font-medium">Subject:</span> {state.subject}</div>
            <div className="mt-2 p-2 bg-beige-50 dark:bg-gray-900 rounded-inner text-[11.5px] text-ink dark:text-white-soft whitespace-pre-wrap">{state.body}</div>
          </div>
        )}

        {error && <div className="text-[11px] text-red-700 bg-red-50 p-1.5 rounded">{error}</div>}

        <div className="flex flex-wrap gap-2 pt-1">
          <button
            onClick={handleSend}
            disabled={status === 'sending'}
            className="px-3 py-1 text-[12px] font-medium bg-ink text-white rounded hover:bg-ink-soft disabled:opacity-50"
          >
            {status === 'sending' ? 'Sending…' : 'Send'}
          </button>
          <button
            onClick={handleMailto}
            className="px-3 py-1 text-[12px] font-medium bg-beige-200 text-ink dark:text-white rounded hover:bg-beige-300"
          >
            Open in email
          </button>
          <button
            onClick={() => setEditing(e => !e)}
            className="px-3 py-1 text-[12px] font-medium bg-beige-100 dark:bg-gray-800 text-ink dark:text-white rounded hover:bg-beige-200"
          >
            {editing ? 'Done editing' : 'Edit'}
          </button>
          <button
            onClick={() => setStatus('cancelled')}
            className="px-3 py-1 text-[12px] text-ink dark:text-white-muted hover:text-ink dark:text-white"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}

function EmailSentCard({ data }) {
  if (!data) return null
  return (
    <div className="rounded-card bg-white dark:bg-gray-800 border border-beige-200 dark:border-gray-700 p-3">
      <div className="text-[12px] font-medium text-ink dark:text-white mb-2">{'\u2709\uFE0F'} Email Sent</div>
      <div className="text-[12px] text-ink dark:text-white-muted space-y-1">
        <div><span className="text-ink dark:text-white font-medium">To:</span> {data.to} {data.to_email ? `(${data.to_email})` : ''}</div>
        <div><span className="text-ink dark:text-white font-medium">Subject:</span> {data.subject}</div>
        <div className="mt-2 p-2 bg-beige-50 dark:bg-gray-900 rounded-inner text-[11.5px] text-ink dark:text-white-soft whitespace-pre-wrap">{data.body}</div>
      </div>
      {data.simulated && (
        <div className="mt-2 text-[10px] text-ink dark:text-white-muted italic">Simulated send (demo mode)</div>
      )}
    </div>
  )
}

function ConnectEmailCard({ data }) {
  // Pre-fill the user's logged-in email from data (set by comms agent)
  const [email, setEmail] = useState(data?.email || '')
  const [password, setPassword] = useState('')
  const [imapHost, setImapHost] = useState(data?.imap_host || 'outlook.office365.com')
  const [imapPort, setImapPort] = useState(data?.imap_port || 993)
  const [smtpHost, setSmtpHost] = useState(data?.smtp_host || 'smtp.office365.com')
  const [smtpPort, setSmtpPort] = useState(data?.smtp_port || 587)
  const [editingEmail, setEditingEmail] = useState(!data?.email)
  const [status, setStatus] = useState('idle') // idle | connecting | connected | failed
  const [error, setError] = useState('')

  const handleConnect = async () => {
    if (!email || !password) {
      setError('Email and app password are required.')
      return
    }
    setStatus('connecting')
    setError('')
    try {
      const token = localStorage.getItem('lumen.token')
      const r = await fetch('/lumen/email/connect', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          email,
          password,
          imap_host: imapHost,
          imap_port: parseInt(imapPort, 10),
          smtp_host: smtpHost,
          smtp_port: parseInt(smtpPort, 10),
        }),
      })
      const result = await r.json()
      if (result.status === 'connected') {
        setStatus('connected')
        setPassword('')  // clear from memory
      } else {
        setStatus('failed')
        setError(result.error || 'Connection failed')
      }
    } catch (e) {
      setStatus('failed')
      setError(e?.message || 'Connection failed')
    }
  }

  if (status === 'connected') {
    return (
      <div className="rounded-card bg-green-50 border border-green-200 p-3">
        <div className="text-[12px] text-green-800">✓ Email connected: <strong>{email}</strong></div>
        <div className="text-[11px] text-green-700 mt-1">You can now say "check my inbox" or "send an email to ..." </div>
      </div>
    )
  }

  return (
    <div className="rounded-card bg-white dark:bg-gray-800 border border-beige-200 dark:border-gray-700 p-3 space-y-2">
      <div className="text-[12px] font-medium text-ink dark:text-white">📧 Connect your email</div>

      {editingEmail ? (
        <label className="block text-[11px] text-ink dark:text-white-muted">Email address
          <input type="email" value={email} onChange={e => setEmail(e.target.value)}
            placeholder="you@college.edu"
            autoFocus
            className="mt-0.5 w-full px-2 py-1 text-[12px] bg-beige-50 dark:bg-gray-900 border border-beige-200 dark:border-gray-700 rounded" />
        </label>
      ) : (
        <div className="rounded-inner bg-beige-50 dark:bg-gray-900 border border-beige-200 dark:border-gray-700 px-2 py-1.5 flex items-center justify-between">
          <div className="text-[12px] text-ink dark:text-white">
            <span className="text-ink dark:text-white-muted">Connecting:</span> <strong>{email}</strong>
          </div>
          <button type="button" onClick={() => setEditingEmail(true)}
            className="text-[10px] text-ink dark:text-white-muted underline">change</button>
        </div>
      )}

      <label className="block text-[11px] text-ink dark:text-white-muted">App password
        <input type="password" value={password} onChange={e => setPassword(e.target.value)}
          placeholder="paste your app password"
          autoFocus={!editingEmail}
          className="mt-0.5 w-full px-2 py-1 text-[12px] bg-beige-50 dark:bg-gray-900 border border-beige-200 dark:border-gray-700 rounded" />
      </label>
      <details className="text-[11px] text-ink dark:text-white-muted">
        <summary className="cursor-pointer">Advanced (IMAP/SMTP servers)</summary>
        <div className="grid grid-cols-2 gap-2 mt-2">
          <label>IMAP host
            <input value={imapHost} onChange={e => setImapHost(e.target.value)}
              className="mt-0.5 w-full px-2 py-1 text-[12px] bg-beige-50 dark:bg-gray-900 border border-beige-200 dark:border-gray-700 rounded" />
          </label>
          <label>IMAP port
            <input value={imapPort} onChange={e => setImapPort(e.target.value)} type="number"
              className="mt-0.5 w-full px-2 py-1 text-[12px] bg-beige-50 dark:bg-gray-900 border border-beige-200 dark:border-gray-700 rounded" />
          </label>
          <label>SMTP host
            <input value={smtpHost} onChange={e => setSmtpHost(e.target.value)}
              className="mt-0.5 w-full px-2 py-1 text-[12px] bg-beige-50 dark:bg-gray-900 border border-beige-200 dark:border-gray-700 rounded" />
          </label>
          <label>SMTP port
            <input value={smtpPort} onChange={e => setSmtpPort(e.target.value)} type="number"
              className="mt-0.5 w-full px-2 py-1 text-[12px] bg-beige-50 dark:bg-gray-900 border border-beige-200 dark:border-gray-700 rounded" />
          </label>
        </div>
      </details>
      {error && <div className="text-[11px] text-red-700 bg-red-50 p-1.5 rounded">{error}</div>}
      <button
        onClick={handleConnect}
        disabled={status === 'connecting'}
        className="w-full py-1.5 bg-ink text-white text-[12px] font-medium rounded hover:opacity-90 disabled:opacity-50">
        {status === 'connecting' ? 'Testing connection…' : 'Connect'}
      </button>
    </div>
  )
}

function OutlookSearchCard({ data }) {
  const query = data?.query || ''
  const mode = data?.mode || 'search' // 'search' or 'recent'
  const [state, setState] = useState({ status: 'pending', results: [], error: '', note: '', source: '' })
  const [actionState, setActionState] = useState({}) // { rowIdx: { status, text } }
  const [summarizingAll, setSummarizingAll] = useState(false)

  useEffect(() => {
    // For 'recent' mode, empty query is OK (we scan the visible inbox)
    if (mode !== 'recent' && !query) return

    // Wait up to 3 seconds for the extension bridge to inject window.lumenExt
    // (it loads at document_start but React may render before the injected
    // <script src="chrome-extension://...page-bridge.js"> finishes executing)
    const waitForExt = () => new Promise((resolve) => {
      if (window.lumenExt?.isInstalled) return resolve(true)
      let elapsed = 0
      const tick = setInterval(() => {
        if (window.lumenExt?.isInstalled) { clearInterval(tick); resolve(true) }
        elapsed += 100
        if (elapsed >= 3000) { clearInterval(tick); resolve(false) }
      }, 100)
      // Also listen for the bridge's ready event in case it fires after we started waiting
      window.addEventListener('lumenExtReady', () => { clearInterval(tick); resolve(true) }, { once: true })
    })

    setState({ status: 'loading', results: [], error: '', note: '', source: '' })
    waitForExt().then(ready => {
      if (!ready) {
        setState({ status: 'no-extension', results: [], error: '', note: '', source: '' })
        return
      }
      return window.lumenExt.searchInbox(query)
        .then(r => {
          if (r?.ok && Array.isArray(r.results)) {
            // Filter out the user's own emails — they almost never want to see their own
            // sent items when asking "find emails from X" or "show my inbox"
            const myEmail = (() => {
              try { return (JSON.parse(localStorage.getItem('lumen.user') || '{}').email || '').toLowerCase() } catch { return '' }
            })()
            const filtered = r.results.filter(row => {
              const se = (row.senderEmail || '').toLowerCase()
              return !myEmail || se !== myEmail
            })
            // If the query was "from:X" and the filtered list is empty, surface that explicitly
            if (filtered.length === 0 && r.results.length > 0) {
              setState({
                status: 'ok', results: [], error: '',
                note: `No emails from external senders matched "${query}" — only your own messages were found.`,
                source: r.source || '',
              })
            } else {
              setState({
                status: 'ok', results: filtered, error: '',
                note: r.note || '', source: r.source || '',
              })
            }
          } else {
            setState({ status: 'error', results: [], error: r?.error || 'Search returned no results', note: '', source: '' })
          }
        })
        .catch(e => setState({ status: 'error', results: [], error: e?.message || 'Search failed', note: '', source: '' }))
    })
  }, [query])

  const summarizeAll = async () => {
    if (summarizingAll) return
    setSummarizingAll(true)
    const indices = state.results.slice(0, 5).map((_, i) => i)
    for (const i of indices) {
      // Skip if already done
      if (actionState[i]?.status === 'done') continue
      setActionState(s => ({ ...s, [i]: { status: 'reading' } }))
      try {
        const opened = await window.lumenExt.selectAndRead(i)
        const email = opened?.data
        if (!email?.body) throw new Error('Could not read email body')
        setActionState(s => ({ ...s, [i]: { status: 'summarizing' } }))
        const out = await callLumenLLM('/lumen/comm/extension/reply', {
          subject: email.subject, sender: email.sender, sender_email: email.senderEmail || '',
          body: email.body,
          instruction: 'Summarize this email in 1-2 short sentences. Return only the summary, plain text.',
        })
        setActionState(s => ({ ...s, [i]: { status: 'done', text: out.reply || '(no summary)' } }))
      } catch (e) {
        setActionState(s => ({ ...s, [i]: { status: 'error', text: e.message } }))
      }
    }
    setSummarizingAll(false)
  }

  const callLumenLLM = async (path, payload) => {
    const token = localStorage.getItem('lumen.token')
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    return r.json()
  }

  const summarizeRow = async (idx) => {
    setActionState(s => ({ ...s, [idx]: { status: 'reading' } }))
    try {
      const opened = await window.lumenExt.selectAndRead(idx)
      const email = opened?.data
      if (!email?.body) throw new Error('Could not read the email body')
      setActionState(s => ({ ...s, [idx]: { status: 'summarizing' } }))
      const out = await callLumenLLM('/lumen/comm/extension/reply', {
        subject: email.subject, sender: email.sender, sender_email: email.senderEmail || '',
        body: email.body,
        instruction: 'Summarize this email in 2-3 sentences. Return only the summary.',
      })
      setActionState(s => ({ ...s, [idx]: { status: 'done', text: out.reply || out.error || '(no result)' } }))
    } catch (e) {
      setActionState(s => ({ ...s, [idx]: { status: 'error', text: e.message } }))
    }
  }

  const replyToRow = async (idx) => {
    const instruction = prompt('How should I reply? e.g. "Politely decline" or "Acknowledge and say I will follow up"')
    if (!instruction) return
    setActionState(s => ({ ...s, [idx]: { status: 'reading' } }))
    try {
      const opened = await window.lumenExt.selectAndRead(idx)
      const email = opened?.data
      if (!email?.body) throw new Error('Could not read the email body')
      setActionState(s => ({ ...s, [idx]: { status: 'generating' } }))
      const out = await callLumenLLM('/lumen/comm/extension/reply', {
        subject: email.subject, sender: email.sender, sender_email: email.senderEmail || '',
        body: email.body, instruction,
      })
      if (!out.reply) throw new Error(out.error || 'No reply generated')
      // Inject into Outlook compose
      await window.lumenExt.injectReply(out.reply)
      setActionState(s => ({ ...s, [idx]: { status: 'done', text: out.reply + '\n\n(Injected into Outlook — click Send there, or use Send Now)' } }))
    } catch (e) {
      setActionState(s => ({ ...s, [idx]: { status: 'error', text: e.message } }))
    }
  }

  if (state.status === 'no-extension') {
    return (
      <div className="rounded-card bg-amber-50 border border-amber-200 p-3 text-[12px] text-amber-900">
        <div className="font-medium mb-1">📦 Lumen for Outlook extension required</div>
        <div className="leading-relaxed">
          To search your Outlook inbox, install the <strong>Lumen for Outlook</strong> Chrome extension and
          open Outlook in another tab. Then ask again.
        </div>
      </div>
    )
  }

  if (state.status === 'loading' || state.status === 'pending') {
    return (
      <div className="rounded-card bg-white dark:bg-gray-800 border border-beige-200 dark:border-gray-700 p-3 text-[12px] text-ink dark:text-white-soft">
        🔍 Searching Outlook for <strong>{query}</strong>…
      </div>
    )
  }

  if (state.status === 'error') {
    return (
      <div className="rounded-card bg-red-50 border border-red-200 p-3 text-[12px] text-red-800">
        ⚠ {state.error}<br/>
        <span className="text-[11px] text-red-600">Make sure Outlook is open in another tab and try again.</span>
      </div>
    )
  }

  if (!state.results.length) {
    return (
      <div className="rounded-card bg-white dark:bg-gray-800 border border-beige-200 dark:border-gray-700 p-3 text-[12px] text-ink dark:text-white-soft">
        🔍 No emails found for <strong>{query}</strong>.
      </div>
    )
  }

  return (
    <div className="rounded-card bg-white dark:bg-gray-800 border border-beige-200 dark:border-gray-700 p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[12px] font-medium text-ink dark:text-white">
          {mode === 'recent' ? '📬 Your recent emails' : <>🔍 Outlook results for <em>{query}</em></>}
        </div>
        <button
          onClick={summarizeAll}
          disabled={summarizingAll}
          className="text-[11px] px-2 py-1 bg-ink text-white rounded hover:opacity-90 disabled:opacity-50">
          {summarizingAll ? 'Summarizing…' : '📝 Summarize all'}
        </button>
      </div>
      {state.note && (
        <div className="text-[10.5px] text-ink dark:text-white-muted mb-2 italic">{state.note}</div>
      )}
      <div className="flex flex-col gap-2">
        {state.results.slice(0, 6).map((row, i) => {
          const act = actionState[i] || {}
          return (
            <div key={i} className="rounded-inner border border-beige-200 dark:border-gray-700 bg-beige-50 dark:bg-gray-900 p-2">
              <div className="text-[12px] text-ink dark:text-white font-medium truncate">{row.subject}</div>
              <div className="text-[11px] text-ink dark:text-white-muted truncate">from {row.sender}</div>
              {row.snippet && <div className="text-[11px] text-ink dark:text-white-soft mt-1 line-clamp-2">{row.snippet}</div>}
              <div className="flex gap-1.5 mt-2">
                <button onClick={() => summarizeRow(i)} disabled={['reading', 'summarizing', 'generating'].includes(act.status)}
                  className="text-[11px] px-2 py-1 bg-ink text-white rounded hover:opacity-90 disabled:opacity-50">
                  📝 Summarize
                </button>
                <button onClick={() => replyToRow(i)} disabled={['reading', 'summarizing', 'generating'].includes(act.status)}
                  className="text-[11px] px-2 py-1 bg-amber text-white rounded hover:opacity-90 disabled:opacity-50">
                  ↩ Reply
                </button>
                {act.status === 'reading' && <span className="text-[11px] text-ink dark:text-white-muted self-center">opening email…</span>}
                {act.status === 'summarizing' && <span className="text-[11px] text-ink dark:text-white-muted self-center">summarizing…</span>}
                {act.status === 'generating' && <span className="text-[11px] text-ink dark:text-white-muted self-center">drafting reply…</span>}
              </div>
              {act.status === 'done' && (
                <div className="mt-2 p-2 bg-white dark:bg-gray-800 border border-beige-200 dark:border-gray-700 rounded text-[11.5px] whitespace-pre-wrap">{act.text}</div>
              )}
              {act.status === 'error' && (
                <div className="mt-2 p-2 bg-red-50 border border-red-200 rounded text-[11px] text-red-700">{act.text}</div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function InboxCard({ data }) {
  if (!data || !Array.isArray(data) || data.length === 0) return null
  return (
    <div className="rounded-card bg-white dark:bg-gray-800 border border-beige-200 dark:border-gray-700 p-3">
      <div className="text-[12px] font-medium text-ink dark:text-white mb-2">{'\ud83d\udcec'} Inbox</div>
      <div className="flex flex-col gap-1.5">
        {data.slice(0, 5).map((m, i) => (
          <div key={i} className="flex items-center gap-2 text-[12px]">
            <span className={`w-2 h-2 rounded-full shrink-0 ${
              m.status === 'unread' ? 'bg-amber' : 'bg-beige-300'
            }`} />
            <span className="text-ink dark:text-white flex-1 truncate">{m.subject || 'No subject'}</span>
            <span className="text-ink dark:text-white-muted text-2xs shrink-0">{m.from || '?'}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function PortfolioFilesCard({ data }) {
  const [deleted, setDeleted] = useState([])
  const [deleting, setDeleting] = useState(null)
  if (!data || !Array.isArray(data.files) || data.files.length === 0) return null

  const mode = data.mode || 'browse'  // 'browse' | 'delete'
  const visible = data.files.filter(f => !deleted.includes(f.path))

  const handleDelete = async (file) => {
    if (!confirm(`Delete "${file.name}"?`)) return
    setDeleting(file.path)
    try {
      await api.portfolioDelete(file.path)
      setDeleted(prev => [...prev, file.path])
    } catch (e) {
      alert(`Delete failed: ${e?.message || 'unknown error'}`)
    } finally { setDeleting(null) }
  }

  const getIcon = (name) => {
    const ext = name.split('.').pop()?.toLowerCase()
    if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg'].includes(ext)) return '🖼️'
    if (['mp4', 'mov', 'avi', 'webm'].includes(ext)) return '🎬'
    if (['pdf'].includes(ext)) return '📄'
    if (['dir', 'directory'].includes(ext)) return '📂'
    return '📎'
  }

  if (visible.length === 0) {
    return (
      <div className="rounded-card bg-white dark:bg-gray-800 border border-beige-200 dark:border-gray-700 p-3">
        <div className="text-[12px] text-ink-muted dark:text-white-muted">All files removed.</div>
      </div>
    )
  }

  return (
    <div className="rounded-card bg-white dark:bg-gray-800 border border-beige-200 dark:border-gray-700 p-3">
      <div className="text-[12px] font-medium text-ink dark:text-white mb-2">💼 Portfolio files</div>
      <div className="flex flex-col gap-1.5">
        {visible.map((f, i) => (
          <div key={f.path || i}
            className="flex items-center gap-2 rounded-inner border border-beige-200 dark:border-gray-700 px-2 py-1.5">
            <span className="text-[14px] shrink-0">{f.type === 'dir' ? '📂' : getIcon(f.name || '')}</span>
            <div className="flex-1 min-w-0">
              <div className="text-[12px] text-ink dark:text-white truncate">{f.name}</div>
              {f.type !== 'dir' && typeof f.size === 'number' && (
                <div className="text-2xs text-ink-muted dark:text-white-muted">{(f.size / 1024).toFixed(1)} KB</div>
              )}
            </div>
            {f.url && (
              <a href={f.url} target="_blank" rel="noopener noreferrer"
                className="text-[11px] px-2 py-0.5 rounded bg-beige-100 dark:bg-gray-700 text-ink dark:text-white hover:bg-beige-200 shrink-0">
                Open ↗
              </a>
            )}
            {mode === 'delete' && f.type !== 'dir' && (
              <button type="button" onClick={() => handleDelete(f)} disabled={deleting === f.path}
                className="text-[11px] px-2 py-0.5 rounded-pill border border-rose-300 text-rose-600 hover:bg-rose-50 disabled:opacity-50 shrink-0">
                {deleting === f.path ? '…' : 'Delete'}
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Shiksha Cards ─────────────────────────────────────────────────────────────

function ShikshaAgentsCard({ data }) {
  if (!data?.agents?.length) return null
  return (
    <div className="rounded-card bg-white dark:bg-gray-800 border border-beige-200 dark:border-gray-700 p-3">
      <div className="text-[12px] font-semibold text-ink dark:text-white mb-2">🎓 Teaching Assistants on Shiksha</div>
      <div className="flex flex-col gap-1.5">
        {data.agents.map((a, i) => (
          <div key={a.agent_id} className="flex items-start gap-2">
            <span className="text-[11px] text-ink dark:text-white-muted w-5 text-right shrink-0 mt-0.5">{i + 1}.</span>
            <div className="flex-1 min-w-0">
              <div className="text-[12px] font-medium text-ink dark:text-white">{a.name}</div>
              <div className="text-[11px] text-ink dark:text-white-muted">{a.subject} · {a.level}</div>
            </div>
            <a
              href={a.url}
              target="_blank"
              rel="noopener noreferrer"
              className="shrink-0 text-[11px] px-2 py-0.5 rounded border border-amber/40 text-amber-700 hover:bg-amber/10 font-medium"
            >
              Open ↗
            </a>
          </div>
        ))}
      </div>
    </div>
  )
}

function ShikshaProgressCard({ data }) {
  const progress = data?.progress || []
  const agents   = data?.agents   || []
  if (!progress.length && !agents.length) return null

  return (
    <div className="rounded-card bg-white dark:bg-gray-800 border border-beige-200 dark:border-gray-700 p-3">
      <div className="text-[12px] font-semibold text-ink dark:text-white mb-2">📊 Your Shiksha Progress</div>
      {progress.length > 0 && (
        <div className="flex flex-col gap-1.5 mb-2">
          {progress.map(p => {
            const lastDate = p.last_active ? new Date(p.last_active).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' }) : null
            return (
              <div key={p.agent_id} className="flex items-center gap-2">
                <div className="flex-1 min-w-0">
                  <div className="text-[12px] font-medium text-ink dark:text-white truncate">{p.name}</div>
                  <div className="text-[11px] text-ink dark:text-white-muted">
                    {p.thread_count} session{p.thread_count !== 1 ? 's' : ''}
                    {lastDate && <> · Last: {lastDate}</>}
                  </div>
                </div>
                <a
                  href={p.continue_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="shrink-0 text-[11px] px-2 py-0.5 rounded border border-emerald-300 text-emerald-700 hover:bg-emerald-50 font-medium"
                >
                  ▶ Continue
                </a>
              </div>
            )
          })}
        </div>
      )}
      {agents.length > 0 && progress.length === 0 && (
        <div className="text-[12px] text-ink dark:text-white-muted">No activity yet. Start a session on Shiksha to track your progress here.</div>
      )}
    </div>
  )
}
