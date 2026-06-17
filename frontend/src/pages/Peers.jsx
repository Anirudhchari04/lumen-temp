import { useEffect, useRef, useState } from 'react'
import TopStrip from '../components/TopStrip.jsx'
import LoadingDots from '../components/LoadingDots.jsx'
import MessageBubble from '../components/MessageBubble.jsx'
import { IconClose, IconArrowUp } from '../components/icons.jsx'
import { api } from '../lib/api.js'

export default function Peers({ user }) {
  const [peers, setPeers]     = useState([])
  const [loading, setLoad]    = useState(true)
  const [err, setErr]         = useState('')
  const [messagePeer, setMP]  = useState(null)
  const [threads, setThreads] = useState([])   // inbox + sent
  const [toast, setToast]     = useState('')

  const name = user?.name?.split(' ')[0] || 'there'
  const initials = (user?.name || user?.email || 'U')
    .split(/[\s@._-]+/).filter(Boolean).slice(0, 2).map(s => s[0]?.toUpperCase()).join('') || 'U'

  useEffect(() => {
    let cancel = false
    Promise.all([
      api.peers().catch(e => ({ __err: e })),
      api.peerMessages().catch(() => ({ inbox: [], sent: [] })),
    ]).then(([p, msgs]) => {
      if (cancel) return
      if (p?.__err) setErr(p.__err.message || 'Failed to load peers')
      else setPeers(p?.peers || [])
      const combined = [...(msgs?.inbox || []), ...(msgs?.sent || [])]
      setThreads(combined)
      setLoad(false)
    })
    return () => { cancel = true }
  }, [])

  const refreshThreads = async () => {
    try {
      const msgs = await api.peerMessages()
      setThreads([...(msgs?.inbox || []), ...(msgs?.sent || [])])
    } catch {}
  }

  return (
    <div className="flex-1 min-w-0 flex flex-col">
      <TopStrip userName={name} userInitials={initials} eventCount={peers.length} />
      <div className="h-px bg-gradient-to-r from-transparent via-beige-200 to-transparent shrink-0" />
      <div className="px-6 pt-5 pb-4 shrink-0" style={{ borderBottom: '0.5px solid rgba(0,0,0,0.07)' }}>
        <h2 className="text-[18px] font-semibold text-ink tracking-tight">Peers</h2>
        <p className="text-[12px] text-ink-muted mt-1">
          Classmates on the network — your Lumen delivers messages to their Lumen via LITP.
        </p>
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        {loading && <LoadingDots label="Discovering peers" />}
        {err && <div className="text-[13px] text-rose-600">{err}</div>}
        {!loading && !err && peers.length === 0 && (
          <div className="rounded-card bg-white border border-beige-200 p-6 text-[13px] text-ink-soft">
            No peers yet. When classmates sign in, they'll appear here automatically.
          </div>
        )}
        {!loading && peers.length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {peers.map(p => (
              <PeerCard
                key={p.id || p.email}
                p={p}
                unread={threads.filter(m => m.from_id === p.id && !m.read).length}
                onMessage={() => setMP(p)}
              />
            ))}
          </div>
        )}
      </div>

      {messagePeer && (
        <MessageDrawer
          peer={messagePeer}
          meId={user?.id}
          existing={threads.filter(m =>
            (m.from_id === messagePeer.id && m.to_id === user?.id) ||
            (m.to_id === messagePeer.id && m.from_id === user?.id)
          )}
          onClose={() => setMP(null)}
          onSent={async () => { await refreshThreads(); setToast('Delivered to their Lumen'); setTimeout(() => setToast(''), 2500) }}
        />
      )}

      {toast && (
        <div className="fixed bottom-6 right-6 z-40 bg-ink text-white text-[12.5px] px-3 py-2 rounded-inner">
          {toast}
        </div>
      )}
    </div>
  )
}

function PeerCard({ p, unread = 0, onMessage }) {
  const display = p.name || p.display_name || p.email || 'Peer'
  const initials = display.split(/[\s@._-]+/).filter(Boolean).slice(0, 2)
    .map(s => s[0]?.toUpperCase()).join('') || 'P'
  const subjects = (p.active_subjects || []).map(s => s.ta_name + ` (L${s.level})`).join(', ')
  const online   = !!(p.online || p.status === 'online')

  return (
    <div className="glass-card rounded-xl p-4 flex flex-col gap-3 transition-all duration-200 hover:shadow-md">
      <div className="flex items-start gap-3">
        <div className="relative shrink-0">
          <div className="w-10 h-10 rounded-full bg-amber text-white flex items-center justify-center text-[13px] font-semibold">
            {initials}
          </div>
          {online && (
            <span className="absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full bg-emerald-500 border-2 border-white" />
          )}
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-[14px] font-semibold text-ink truncate">{display}</div>
          <div className="text-[11.5px] text-ink-muted mt-0.5 truncate">
            {subjects || 'New on the network'}
          </div>
          <div className="text-[11px] text-ink-muted/70 mt-1 tabular-nums">
            {p.total_sessions || 0} sessions · {p.tcs_mastered || 0} mastered
          </div>
        </div>
      </div>
      <button
        onClick={onMessage}
        className="w-full rounded-xl text-[12.5px] font-medium py-2 flex items-center justify-center gap-2 transition-all duration-150 active:scale-[0.98]"
        style={{
          background: 'rgba(200,137,42,0.08)',
          border: '0.5px solid rgba(200,137,42,0.25)',
          color: '#c8892a',
        }}
        onMouseEnter={e => { e.currentTarget.style.background = '#c8892a'; e.currentTarget.style.color = 'white' }}
        onMouseLeave={e => { e.currentTarget.style.background = 'rgba(200,137,42,0.08)'; e.currentTarget.style.color = '#c8892a' }}
      >
        Message
        {unread > 0 && (
          <span className="inline-flex items-center justify-center min-w-[18px] h-[18px] px-1.5 text-[10px] rounded-full bg-amber text-white font-semibold">
            {unread}
          </span>
        )}
      </button>
    </div>
  )
}

function MessageDrawer({ peer, meId, existing = [], onClose, onSent }) {
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const [err, setErr] = useState('')
  const [msgs, setMsgs] = useState(existing)
  const endRef = useRef(null)

  // Sync only when thread length grows (new messages from parent), not on every re-render
  useEffect(() => {
    if (existing.length > msgs.length) setMsgs(existing)
  }, [existing.length]) // eslint-disable-line react-hooks/exhaustive-deps

  // Poll for new messages (auto-replies arrive ~2-3s after sending)
  useEffect(() => {
    const poll = setInterval(async () => {
      try {
        const r = await api.peerMessages()
        const all = [...(r?.inbox || []), ...(r?.sent || [])]
        const thread = all.filter(m =>
          (m.from_id === peer.id && m.to_id === meId) ||
          (m.to_id === peer.id && m.from_id === meId)
        )
        if (thread.length > msgs.length) setMsgs(thread)
      } catch {}
    }, 3000)
    return () => clearInterval(poll)
  }, [peer.id, meId, msgs.length])

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [msgs.length])

  const send = async (e) => {
    e?.preventDefault?.()
    const t = text.trim(); if (!t || sending) return
    setSending(true); setErr('')
    try {
      const r = await api.sendPeerMessage(peer.id, t)
      const sent = r?.message || { from_id: meId, to_id: peer.id, message: t, created_at: new Date().toISOString() }
      setMsgs(m => [...m, sent])
      setText('')
      onSent?.()
    } catch (e) {
      setErr(e?.message || 'Send failed')
    } finally { setSending(false) }
  }

  return (
    <div className="fixed inset-0 z-30 flex justify-end">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <aside
        className="glass-surface relative w-full sm:w-[400px] h-full flex flex-col"
        style={{ borderLeft: '0.5px solid rgba(255,255,255,0.55)' }}
      >
        <div className="px-5 py-4 flex items-center justify-between shrink-0" style={{ borderBottom: '0.5px solid rgba(0,0,0,0.07)' }}>
          <div className="min-w-0">
            <div className="text-[14.5px] font-semibold text-ink truncate">
              {peer.name || peer.display_name || 'Peer'}
            </div>
            <div className="text-[11.5px] text-ink-muted truncate mt-0.5">
              Your Lumen → {peer.name ? `${peer.name.split(' ')[0]}'s Lumen` : 'their Lumen'}
            </div>
          </div>
          <button onClick={onClose} className="w-8 h-8 rounded-xl hover:bg-beige-200 flex items-center justify-center transition-colors">
            <IconClose />
          </button>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto p-4 flex flex-col gap-1.5">
          {msgs.length === 0 && (
            <div className="text-[12.5px] text-ink-muted italic text-center mt-4">
              No messages yet. Say hi — your Lumen will deliver it via LITP.
            </div>
          )}
          {(() => {
            const sorted = [...msgs].sort((a, b) => new Date(a.created_at || 0) - new Date(b.created_at || 0))
            const userInfo = (() => { try { return JSON.parse(localStorage.getItem('lumen.user') || '{}') } catch { return {} } })()
            const myInitial = (userInfo.name || userInfo.email || 'M').trim()[0]?.toUpperCase() || 'M'
            const peerInitial = (peer.name || peer.display_name || 'P').trim()[0]?.toUpperCase() || 'P'
            const dayKey = (iso) => {
              try {
                const d = new Date(iso || 0)
                return d.toDateString()
              } catch { return '' }
            }
            const friendlyDay = (iso) => {
              try {
                const d = new Date(iso || 0)
                const now = new Date()
                const diffDays = Math.floor((now.setHours(0,0,0,0) - new Date(d).setHours(0,0,0,0)) / 86400000)
                if (diffDays === 0) return 'Today'
                if (diffDays === 1) return 'Yesterday'
                if (diffDays < 7) return d.toLocaleDateString([], { weekday: 'long' })
                return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
              } catch { return '' }
            }
            const items = []
            let lastDay = null
            sorted.forEach((m, i) => {
              const mine = m.from_id === meId
              const prevMine = i > 0 ? sorted[i - 1].from_id === meId : null
              const isFirstInGroup = i === 0 || mine !== prevMine
              const isFirstInBlockSameSide = i === 0 || mine !== prevMine
              const day = dayKey(m.created_at)
              if (day !== lastDay) {
                items.push(
                  <div key={`day-${i}`} className="flex items-center justify-center my-2">
                    <span className="text-[10px] uppercase tracking-wide text-ink-muted bg-beige-100 dark:bg-gray-800 px-2 py-0.5 rounded-full">
                      {friendlyDay(m.created_at)}
                    </span>
                  </div>
                )
                lastDay = day
              }
              items.push(
                <div key={m.id || i} className={`flex items-end gap-2 ${mine ? 'flex-row-reverse' : 'flex-row'}`}>
                  {/* Avatar — only on first message of a consecutive block */}
                  {isFirstInBlockSameSide ? (
                    <div className={`w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-semibold shrink-0 ${
                      mine ? 'bg-amber text-white' : 'bg-beige-200 dark:bg-gray-700 text-ink-soft dark:text-white-soft'
                    }`}>
                      {mine ? myInitial : peerInitial}
                    </div>
                  ) : (
                    <div className="w-7 shrink-0" />
                  )}
                  <div className="min-w-0 max-w-[78%]">
                    <MessageBubble
                      role={mine ? 'user' : 'lumen'}
                      content={m.message}
                      timestamp={m.created_at}
                      senderLabel={!mine && isFirstInGroup ? (m.sender_display || `${m.from_name || peer.name?.split(' ')[0] || 'Peer'}'s Lumen`) : undefined}
                      small
                    />
                  </div>
                </div>
              )
            })
            return items
          })()}
          <div ref={endRef} />
        </div>
        {err && <div className="px-4 pb-1 text-[11.5px] text-rose-600">{err}</div>}

        {/* Quick prompts — try peer messaging (the last one exercises the private-info approval flow) */}
        <div className="flex items-center gap-1.5 px-3 pt-2 overflow-x-auto no-scrollbar">
          {['Want to study together?', 'What are you working on?', 'How far are you in your course?', 'Can I get your phone number?'].map(c => (
            <button key={c} type="button" onClick={() => setText(c)}
              className="shrink-0 rounded-full border border-beige-200 dark:border-gray-700 bg-beige-50 dark:bg-gray-800 text-ink-soft dark:text-white-soft hover:text-ink dark:hover:text-white hover:bg-beige-100 transition-all whitespace-nowrap"
              style={{ fontSize: 11, padding: '4px 10px' }}>
              {c}
            </button>
          ))}
        </div>

        <RequestInfoRow peer={peer} onSent={onSent} />

        <form onSubmit={send} className="p-3 flex items-center gap-2 shrink-0" style={{ borderTop: '0.5px solid rgba(0,0,0,0.07)' }}>
          <input
            value={text} onChange={e => setText(e.target.value)}
            placeholder="Your Lumen will deliver this…"
            className="flex-1 rounded-2xl bg-white border border-beige-200 px-3.5 py-2 text-[12.5px] outline-none focus:border-amber/60 focus:ring-2 focus:ring-amber/10 transition-all shadow-sm"
          />
          <button type="submit" disabled={!text.trim() || sending}
            className="w-9 h-9 rounded-xl bg-amber text-white flex items-center justify-center hover:bg-amber/90 active:scale-95 disabled:opacity-40 transition-all"
            aria-label="Send">
            <IconArrowUp />
          </button>
        </form>
      </aside>
    </div>
  )
}


// Inline row above the compose box — lets the user request a private field from the peer.
function RequestInfoRow({ peer, onSent }) {
  const [open, setOpen] = useState(false)
  const [sending, setSending] = useState(false)
  const [status, setStatus] = useState('')   // e.g. "Requested phone number"

  const request = async (field, label) => {
    setSending(true); setStatus('')
    try {
      const r = await api.requestInfo(peer.id, field)
      if (r?.status === 'auto_granted') {
        setStatus(`${label} shared (it's public)`)
      } else {
        setStatus(`Requested ${label} — waiting for approval`)
      }
      setOpen(false)
      onSent?.()
    } catch (e) {
      setStatus(e?.message || 'Request failed')
    } finally { setSending(false); setTimeout(() => setStatus(''), 4000) }
  }

  const fields = [
    { key: 'phone',      label: 'phone number' },
    { key: 'address',    label: 'address' },
    { key: 'dob',        label: 'date of birth' },
    { key: 'occupation', label: 'occupation' },
  ]

  return (
    <div className="px-3 py-2 border-t border-beige-200 bg-beige-50">
      <div className="flex items-center gap-2">
        <button
          type="button" disabled={sending}
          onClick={() => setOpen(o => !o)}
          className="text-[11px] rounded-pill bg-white border border-beige-300 hover:border-amber-border px-2.5 py-1 text-ink-soft disabled:opacity-50"
          title="Ask their Lumen to share a private detail"
        >
          + Request info
        </button>
        {status && <span className="text-[10.5px] text-ink-muted">{status}</span>}
      </div>
      {open && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {fields.map(f => (
            <button
              key={f.key}
              onClick={() => request(f.key, f.label)}
              disabled={sending}
              className="text-[10.5px] rounded-pill bg-amber-light border border-amber-border text-amber hover:bg-amber hover:text-white px-2 py-0.5 transition-colors disabled:opacity-50">
              {f.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
