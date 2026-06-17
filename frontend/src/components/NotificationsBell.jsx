import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api.js'

const FIELD_LABEL = {
  phone: 'phone number', address: 'address',
  dob: 'date of birth', occupation: 'occupation', bio: 'bio',
}

// Bell in IconRail. Polls a unified proactive feed:
//   - pending info-requests (Yes/No approval)
//   - calendar reminders (5-15 min before)
//   - unread peer-Lumen messages
export default function NotificationsBell() {
  const nav = useNavigate()
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState([])
  const [busy, setBusy] = useState({})
  const panelRef = useRef(null)

  const refresh = async () => {
    try {
      const r = await api.notificationsFeed()
      setItems(r?.items || [])
    } catch {}
  }

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 15000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    if (!open) return
    const onDoc = (e) => { if (panelRef.current && !panelRef.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  const respondRequest = async (it, accept) => {
    setBusy(b => ({ ...b, [it.id]: true }))
    try {
      await api.respondInfoRequest(it.id, accept)
      setItems(xs => xs.filter(x => x.id !== it.id))
    } catch {}
    finally { setBusy(b => { const n = { ...b }; delete n[it.id]; return n }) }
  }

  const dismissCalendar = async (it) => {
    setBusy(b => ({ ...b, [it.id]: true }))
    try {
      await api.markFeedRead('calendar', [it.id])
      setItems(xs => xs.filter(x => x.id !== it.id))
    } catch {}
    finally { setBusy(b => { const n = { ...b }; delete n[it.id]; return n }) }
  }

  const openMessage = async (it) => {
    setBusy(b => ({ ...b, [it.id]: true }))
    try {
      await api.markFeedRead('message', [it.id])
      setItems(xs => xs.filter(x => x.id !== it.id))
      setOpen(false)
      nav('/peers')
    } catch {}
    finally { setBusy(b => { const n = { ...b }; delete n[it.id]; return n }) }
  }

  const openCalendar = () => { setOpen(false); window.open('/ta/calendar', '_blank') }

  const dismissSpike = async (it) => {
    setBusy(b => ({ ...b, [it.id]: true }))
    try {
      await api.markFeedRead('spike', [it.id])
      setItems(xs => xs.filter(x => x.id !== it.id))
    } catch {}
    finally { setBusy(b => { const n = { ...b }; delete n[it.id]; return n }) }
  }

  const openSpike = async (it) => {
    try { await api.markFeedRead('spike', [it.id]) } catch {}
    setItems(xs => xs.filter(x => x.id !== it.id))
    setOpen(false)
    nav('/usage')
  }

  const count = items.length

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        aria-label="Notifications"
        title="Notifications"
        className={`w-[34px] h-[34px] rounded-inner flex items-center justify-center relative
          ${count > 0 ? 'text-amber' : 'text-ink-soft'} hover:bg-beige-200`}
      >
        <BellIcon />
        {count > 0 && (
          <span className="absolute -top-0.5 -right-0.5 min-w-[16px] h-[16px] px-1 rounded-full bg-amber text-white text-[9px] font-medium flex items-center justify-center">
            {count > 9 ? '9+' : count}
          </span>
        )}
      </button>

      {open && (
        <div
          ref={panelRef}
          className="absolute right-0 top-[calc(100%+6px)] w-[340px] z-40"
          style={{
            background: 'rgba(255,255,255,0.92)',
            backdropFilter: 'blur(16px)',
            WebkitBackdropFilter: 'blur(16px)',
            border: '0.5px solid rgba(255,255,255,0.80)',
            borderRadius: 16,
            boxShadow: '0 8px 32px rgba(0,0,0,0.13)',
          }}
        >
          <div className="px-4 py-3 border-b border-beige-200 flex items-center justify-between">
            <div>
              <div className="text-[13px] font-medium text-ink">Notifications</div>
              <div className="text-[10.5px] text-ink-muted">Reminders, peer messages, info requests</div>
            </div>
            <button onClick={refresh} className="text-[10.5px] text-amber hover:underline">Refresh</button>
          </div>
          <div className="max-h-[65vh] overflow-y-auto">
            {items.length === 0 && (
              <div className="px-4 py-6 text-center text-[12px] text-ink-muted">
                All caught up.
              </div>
            )}

            {items.map(it => {
              if (it.type === 'info_request') {
                const r = it.payload
                return (
                  <div key={it.id} className="px-4 py-3 border-b border-beige-100">
                    <div className="flex items-start gap-2">
                      <span className="text-[12px]" title="info request">🔑</span>
                      <div className="flex-1">
                        <div className="text-[12.5px] text-ink leading-snug">
                          <b>{r.from_name}</b>'s Lumen is asking for your <b>{FIELD_LABEL[r.field] || r.field}</b>.
                        </div>
                        {r.reason && <div className="text-[11px] text-ink-muted mt-1 italic">"{r.reason}"</div>}
                        <div className="text-[10.5px] text-ink-muted mt-1">{relTime(it.created_at)}</div>
                      </div>
                    </div>
                    <div className="mt-2 flex gap-2">
                      <button disabled={!!busy[it.id]} onClick={() => respondRequest(it, true)}
                        className="flex-1 text-[11.5px] rounded-pill bg-amber text-white px-3 py-1 hover:opacity-90 disabled:opacity-50">
                        Yes, share
                      </button>
                      <button disabled={!!busy[it.id]} onClick={() => respondRequest(it, false)}
                        className="flex-1 text-[11.5px] rounded-pill bg-beige-100 border border-beige-300 text-ink-soft px-3 py-1 hover:bg-beige-200 disabled:opacity-50">
                        Decline
                      </button>
                    </div>
                  </div>
                )
              }

              if (it.type.startsWith('calendar_')) {
                const isStart = it.type === 'calendar_starting'
                return (
                  <div key={it.id} className="px-4 py-3 border-b border-beige-100">
                    <div className="flex items-start gap-2">
                      <span className="text-[12px]">{isStart ? '⏰' : '🔔'}</span>
                      <div className="flex-1 min-w-0">
                        <div className="text-[12.5px] text-ink leading-snug">{it.title}</div>
                        <div className="text-[10.5px] text-ink-muted mt-0.5">{it.subtitle} · {relTime(it.created_at)}</div>
                      </div>
                    </div>
                    <div className="mt-2 flex gap-2">
                      <button disabled={!!busy[it.id]} onClick={openCalendar}
                        className="flex-1 text-[11.5px] rounded-pill bg-amber text-white px-3 py-1 hover:opacity-90 disabled:opacity-50">
                        Open calendar
                      </button>
                      <button disabled={!!busy[it.id]} onClick={() => dismissCalendar(it)}
                        className="text-[11.5px] rounded-pill bg-beige-100 border border-beige-300 text-ink-soft px-3 py-1 hover:bg-beige-200 disabled:opacity-50">
                        Dismiss
                      </button>
                    </div>
                  </div>
                )
              }

              if (it.type === 'cost_spike') {
                const s = it.payload || {}
                const cost = '$' + (s.cost_usd || 0).toFixed(4)
                return (
                  <div key={it.id} className="px-4 py-3 border-b border-beige-100">
                    <div className="flex items-start gap-2">
                      <span className="text-[12px]" title="cost spike">💸</span>
                      <div className="flex-1 min-w-0">
                        <div className="text-[12.5px] text-ink leading-snug">
                          <b>{s.source}</b> call cost <b>{cost}</b>
                        </div>
                        <div className="text-[11px] text-ink-muted mt-0.5">
                          {s.reason} · {(s.model || '').includes('5.4') ? 'premium model' : s.model}
                        </div>
                        <div className="text-[10.5px] text-ink-muted mt-0.5">{relTime(it.created_at)}</div>
                      </div>
                    </div>
                    <div className="mt-2 flex gap-2">
                      <button disabled={!!busy[it.id]} onClick={() => openSpike(it)}
                        className="flex-1 text-[11.5px] rounded-pill bg-amber text-white px-3 py-1 hover:opacity-90 disabled:opacity-50">
                        View usage
                      </button>
                      <button disabled={!!busy[it.id]} onClick={() => dismissSpike(it)}
                        className="text-[11.5px] rounded-pill bg-beige-100 border border-beige-300 text-ink-soft px-3 py-1 hover:bg-beige-200 disabled:opacity-50">
                        Dismiss
                      </button>
                    </div>
                  </div>
                )
              }

              if (it.type === 'peer_message') {
                return (
                  <button key={it.id} disabled={!!busy[it.id]}
                    onClick={() => openMessage(it)}
                    className="w-full text-left px-4 py-3 border-b border-beige-100 hover:bg-beige-50 disabled:opacity-50">
                    <div className="flex items-start gap-2">
                      <span className="text-[12px]">💬</span>
                      <div className="flex-1 min-w-0">
                        <div className="text-[12.5px] text-ink font-medium truncate">{it.title}</div>
                        <div className="text-[11px] text-ink-soft truncate">{it.subtitle}</div>
                        <div className="text-[10.5px] text-ink-muted mt-0.5">{relTime(it.created_at)}</div>
                      </div>
                    </div>
                  </button>
                )
              }

              return null
            })}
          </div>
        </div>
      )}
    </div>
  )
}

function BellIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
      <path d="M10 21a2 2 0 0 0 4 0" />
    </svg>
  )
}

function relTime(iso) {
  try {
    const d = new Date(iso); const s = Math.floor((Date.now() - d.getTime()) / 1000)
    if (s < 60) return 'just now'
    if (s < 3600) return Math.floor(s / 60) + 'm ago'
    if (s < 86400) return Math.floor(s / 3600) + 'h ago'
    return Math.floor(s / 86400) + 'd ago'
  } catch { return '' }
}
