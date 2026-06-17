import { useEffect, useMemo, useRef, useState } from 'react'
import TopStrip from '../components/TopStrip.jsx'
import LoadingDots from '../components/LoadingDots.jsx'
import MessageBubble from '../components/MessageBubble.jsx'
import { IconArrowUp, IconCheck, IconClose } from '../components/icons.jsx'
import { api } from '../lib/api.js'

// Event type → colour (tailwind utility classes)
const TYPE_STYLE = {
  study:    { bar: 'bg-indigo-500',  dot: 'bg-indigo-500',  chip: 'bg-indigo-50 text-indigo-700 border-indigo-200' },
  reminder: { bar: 'bg-amber',       dot: 'bg-amber',       chip: 'bg-amber-light text-amber border-amber-border' },
  deadline: { bar: 'bg-rose-500',    dot: 'bg-rose-500',    chip: 'bg-rose-50 text-rose-700 border-rose-200' },
  exam:     { bar: 'bg-rose-600',    dot: 'bg-rose-600',    chip: 'bg-rose-50 text-rose-700 border-rose-200' },
  meeting:  { bar: 'bg-emerald-500', dot: 'bg-emerald-500', chip: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
  holiday:  { bar: 'bg-purple-500',  dot: 'bg-purple-500',  chip: 'bg-purple-50 text-purple-700 border-purple-200' },
  other:    { bar: 'bg-slate-400',   dot: 'bg-slate-400',   chip: 'bg-beige-100 text-ink-muted border-beige-300' },
}
const typeStyle = (t) => TYPE_STYLE[(t || 'reminder').toLowerCase()] || TYPE_STYLE.reminder

export default function Calendar({ user }) {
  const [events, setEvents]   = useState([])
  const [prefs, setPrefs]     = useState({ default_remind_minutes: 15, notify_on_start: true })
  const [loading, setLoad]    = useState(true)
  const [cursor, setCursor]   = useState(() => new Date())
  const [selected, setSel]    = useState(null)   // { date, events }

  const name = user?.name?.split(' ')[0] || 'there'
  const initials = (user?.name || user?.email || 'U')
    .split(/[\s@._-]+/).filter(Boolean).slice(0, 2).map(s => s[0]?.toUpperCase()).join('') || 'U'

  const reload = async () => {
    setLoad(true)
    try {
      const [ev, pr] = await Promise.all([
        api.calEvents().catch(() => ({ events: [] })),
        api.calPrefs().catch(() => ({})),
      ])
      setEvents(ev?.events || ev || [])
      if (pr && Object.keys(pr).length) setPrefs(p => ({ ...p, ...pr }))
    } finally { setLoad(false) }
  }
  useEffect(() => { reload() }, [])

  const savePrefs = async (next) => {
    setPrefs(next)
    try { await api.setCalPrefs(next) } catch {}
  }

  const year  = cursor.getFullYear()
  const month = cursor.getMonth()
  const first = new Date(year, month, 1)
  const start = new Date(year, month, 1 - first.getDay())
  const days  = Array.from({ length: 42 }, (_, i) => {
    const d = new Date(start); d.setDate(start.getDate() + i); return d
  })

  const eventsForDay = (d) => events.filter(e => sameDay(parseEventDate(e), d))
  const monthName = cursor.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })

  const upcoming = useMemo(() => {
    const now = new Date()
    const list = events
      .map(e => ({ ...e, _dt: parseEventDate(e) }))
      .filter(e => e._dt && e._dt >= new Date(now.getFullYear(), now.getMonth(), now.getDate()))
      .sort((a, b) => a._dt - b._dt)
    return list.slice(0, 6)
  }, [events])

  const removeEvent = async (id) => {
    try { await api.deleteEvent(id) } catch {}
    setEvents(evs => evs.filter(e => e.id !== id))
    setSel(s => s && ({ ...s, events: s.events.filter(e => e.id !== id) }))
  }
  const toggleDone = async (ev) => {
    const next = ev.status === 'completed' ? 'scheduled' : 'completed'
    try { await api.updateEventStatus(ev.id, next) } catch {}
    setEvents(evs => evs.map(e => e.id === ev.id ? { ...e, status: next } : e))
    setSel(s => s && ({ ...s, events: s.events.map(e => e.id === ev.id ? { ...e, status: next } : e) }))
  }

  return (
    <div className="flex-1 min-w-0 flex flex-col">
      <TopStrip userName={name} userInitials={initials} eventCount={events.length} />
      <div className="px-6 pb-3 flex items-center justify-between">
        <div>
          <h2 className="text-[15px] font-medium text-ink">Calendar</h2>
          <p className="text-2xs text-ink-muted mt-0.5">
            Type into the agent and your event appears here — reminders fire based on your prefs.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setCursor(new Date())}
            className="px-2.5 py-1 text-[12px] rounded-pill bg-beige-100 border border-beige-300 hover:bg-beige-200">Today</button>
          <button onClick={() => setCursor(new Date(year, month - 1, 1))}
            className="px-2 py-1 text-[12px] rounded-pill bg-beige-100 border border-beige-300 hover:bg-beige-200">‹</button>
          <span className="text-[13px] font-medium text-ink min-w-[130px] text-center">{monthName}</span>
          <button onClick={() => setCursor(new Date(year, month + 1, 1))}
            className="px-2 py-1 text-[12px] rounded-pill bg-beige-100 border border-beige-300 hover:bg-beige-200">›</button>
        </div>
      </div>
      <div className="h-px bg-beige-200" />

      <div className="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-[1fr_340px]">
        <div className="p-5 overflow-y-auto">
          {/* Compact month grid */}
          <div className="grid grid-cols-7 gap-[3px] text-[10px] uppercase tracking-wide text-ink-muted mb-1">
            {['Sun','Mon','Tue','Wed','Thu','Fri','Sat'].map((d, i) => (
              <div key={i} className="px-2 py-0.5">{d}</div>
            ))}
          </div>
          <div className="grid grid-cols-7 gap-[3px]">
            {days.map((d, i) => {
              const inMonth = d.getMonth() === month
              const today   = sameDay(d, new Date())
              const dayEv   = eventsForDay(d)
              const isSel   = selected && sameDay(selected.date, d)
              return (
                <button key={i} onClick={() => setSel({ date: d, events: dayEv })}
                  className={`h-[72px] rounded-inner border text-left p-1 flex flex-col overflow-hidden transition-colors ${
                    inMonth ? 'bg-white border-beige-200' : 'bg-beige-50 border-beige-100 text-ink-muted'
                  } ${today ? 'border-amber' : ''} ${isSel ? 'ring-1 ring-amber' : ''} hover:border-amber-border`}>
                  <span className={`text-[10.5px] leading-none ${today ? 'text-amber font-medium' : ''}`}>{d.getDate()}</span>
                  <div className="mt-0.5 flex flex-col gap-[2px] overflow-hidden">
                    {dayEv.slice(0, 2).map((e) => (
                      <span key={e.id}
                        className={`truncate text-[9.5px] leading-tight px-1 py-[1px] rounded text-white ${typeStyle(e.type).bar} ${e.status === 'completed' ? 'opacity-50 line-through' : ''}`}
                        title={e.title}
                      >
                        {e.time && e.time !== 'TBD' ? `${e.time} ` : ''}{e.title}
                      </span>
                    ))}
                    {dayEv.length > 2 && (
                      <span className="text-[9px] text-ink-muted px-1">+{dayEv.length - 2} more</span>
                    )}
                  </div>
                </button>
              )
            })}
          </div>

          {loading && <div className="mt-4"><LoadingDots /></div>}

          {/* Selected day events */}
          {selected && (
            <div className="mt-5">
              <div className="flex items-center justify-between mb-2">
                <div className="text-[13px] font-medium text-ink">
                  {selected.date.toDateString()}
                </div>
                <button onClick={() => setSel(null)}
                  className="text-2xs text-ink-muted hover:text-ink">clear</button>
              </div>
              {selected.events.length === 0 ? (
                <div className="text-2xs text-ink-muted italic">No events. Ask the agent to schedule one.</div>
              ) : (
                <div className="flex flex-col gap-2">
                  {selected.events.map((e) => <EventCard key={e.id} ev={e} onDone={toggleDone} onDelete={removeEvent} />)}
                </div>
              )}
            </div>
          )}

          {/* Upcoming events */}
          <div className="mt-6">
            <div className="text-[13px] font-medium text-ink mb-2">Upcoming</div>
            {upcoming.length === 0 ? (
              <div className="text-2xs text-ink-muted italic">Nothing coming up. Try: "remind me to revise calculus tomorrow at 6pm".</div>
            ) : (
              <div className="flex flex-col gap-1.5">
                {upcoming.map(e => <UpcomingRow key={e.id} ev={e} onDone={toggleDone} onDelete={removeEvent} />)}
              </div>
            )}
          </div>
        </div>

        <aside className="border-t lg:border-t-0 lg:border-l border-beige-200 bg-beige-50 flex flex-col min-h-0">
          <div className="p-4 border-b border-beige-200">
            <h3 className="text-[13px] font-medium text-ink mb-3">Reminder preferences</h3>
            <label className="block text-2xs text-ink-muted mb-1">Notify Lumen before an event</label>
            <select
              value={prefs.default_remind_minutes ?? 15}
              onChange={e => savePrefs({ ...prefs, default_remind_minutes: Number(e.target.value) })}
              className="w-full rounded-inner bg-white border border-beige-200 px-2 py-1.5 text-[13px]"
            >
              <option value={5}>5 minutes before</option>
              <option value={15}>15 minutes before</option>
              <option value={30}>30 minutes before</option>
              <option value={60}>1 hour before</option>
              <option value={1440}>1 day before</option>
            </select>
            <label className="mt-3 flex items-center gap-2 text-[12.5px] text-ink-soft">
              <input type="checkbox"
                checked={!!prefs.notify_on_start}
                onChange={e => savePrefs({ ...prefs, notify_on_start: e.target.checked })}
              />
              Also notify when the event starts
            </label>
          </div>

          <AgentChatPanel onCreated={reload} selectedDate={selected?.date} />
        </aside>
      </div>
    </div>
  )
}

function EventCard({ ev, onDone, onDelete }) {
  const s = typeStyle(ev.type)
  const done = ev.status === 'completed'
  return (
    <div className={`rounded-card bg-white border border-beige-200 p-3 flex items-start gap-3 ${done ? 'opacity-60' : ''}`}>
      <span className={`mt-1 w-2 h-2 rounded-full ${s.dot}`} />
      <div className="flex-1 min-w-0">
        <div className={`text-[13px] font-medium text-ink ${done ? 'line-through' : ''}`}>{ev.title}</div>
        <div className="text-2xs text-ink-muted mt-0.5 flex gap-2 flex-wrap">
          {ev.time && ev.time !== 'TBD' && <span>{ev.time}</span>}
          {ev.duration_mins && ev.duration_mins !== 60 && <span>· {ev.duration_mins} min</span>}
          <span className={`px-1.5 py-[1px] rounded border text-[10px] ${s.chip}`}>{ev.type}</span>
          {ev.reminder_minutes_before && (
            <span className="text-ink-muted">· remind {ev.reminder_minutes_before}m before</span>
          )}
        </div>
        {ev.description && ev.description !== ev.title && (
          <div className="text-[12px] text-ink-soft mt-1">{ev.description}</div>
        )}
      </div>
      <div className="flex gap-1 shrink-0">
        <button onClick={() => onDone(ev)} title={done ? 'Reopen' : 'Mark done'}
          className="w-7 h-7 rounded-full bg-beige-100 hover:bg-beige-200 text-ink flex items-center justify-center">
          <IconCheck />
        </button>
        <button onClick={() => onDelete(ev.id)} title="Delete"
          className="w-7 h-7 rounded-full bg-beige-100 hover:bg-rose-100 text-ink hover:text-rose-600 flex items-center justify-center">
          <IconClose />
        </button>
      </div>
    </div>
  )
}

function UpcomingRow({ ev, onDone, onDelete }) {
  const s = typeStyle(ev.type)
  const dt = parseEventDate(ev)
  const done = ev.status === 'completed'
  const label = dt
    ? dt.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) +
      (ev.time && ev.time !== 'TBD' ? ` · ${ev.time}` : '')
    : 'TBD'
  return (
    <div className={`flex items-center gap-2 bg-white border border-beige-200 rounded-inner px-2.5 py-1.5 ${done ? 'opacity-60' : ''}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />
      <div className="flex-1 min-w-0">
        <div className={`text-[12.5px] text-ink truncate ${done ? 'line-through' : ''}`}>{ev.title}</div>
        <div className="text-[10.5px] text-ink-muted">{label}</div>
      </div>
      <button onClick={() => onDone(ev)} title={done ? 'Reopen' : 'Mark done'}
        className="w-6 h-6 rounded-full bg-beige-100 hover:bg-beige-200 flex items-center justify-center"><IconCheck /></button>
      <button onClick={() => onDelete(ev.id)} title="Delete"
        className="w-6 h-6 rounded-full bg-beige-100 hover:bg-rose-100 hover:text-rose-600 flex items-center justify-center"><IconClose /></button>
    </div>
  )
}

const QUICK_CHIPS = [
  'Remind me to revise today at 6pm',
  'Schedule a study session tomorrow 4pm',
  'What do I have this week?',
  'Clear completed events',
]

function AgentChatPanel({ onCreated, selectedDate }) {
  const [msgs, setMsgs] = useState([
    { role: 'agent', content: 'Tell me what you need — "remind me to study physics at 6pm tomorrow", "schedule a group call Friday 3pm", "what do I have this week?".' },
  ])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const endRef = useRef(null)
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [msgs.length, busy])

  const doSend = async (raw) => {
    const t = (raw ?? text).trim(); if (!t || busy) return
    setMsgs(m => [...m, { role: 'user', content: t }])
    setText(''); setBusy(true)
    try {
      const r = await api.scheduleNatural(t)
      const reply = r?.reply || (r?.event
        ? `Scheduled: ${r.event.title || 'event'} at ${r.event.date} ${r.event.time || ''}`.trim()
        : 'Done.')
      setMsgs(m => [...m, { role: 'agent', content: reply }])
      onCreated?.()
    } catch {
      setMsgs(m => [...m, { role: 'agent', content: "I couldn't reach the calendar — try again." }])
    } finally { setBusy(false) }
  }

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      <div className="px-4 pt-3 pb-2 text-[13px] font-medium text-ink">Calendar agent</div>
      <div className="flex-1 min-h-0 overflow-y-auto px-4 flex flex-col gap-2">
        {msgs.map((m, i) => (
          <MessageBubble key={i} role={m.role === 'user' ? 'user' : 'lumen'} content={m.content} />
        ))}
        {busy && <LoadingDots />}
        <div ref={endRef} />
      </div>
      <div className="px-3 pt-2 flex gap-1.5 overflow-x-auto scrollbar-none">
        {QUICK_CHIPS.map((q, i) => (
          <button key={i} type="button" onClick={() => doSend(q)}
            className="shrink-0 text-[11px] px-2.5 py-1 rounded-pill bg-beige-100 border border-beige-300 text-ink-soft hover:bg-beige-200">
            {q}
          </button>
        ))}
      </div>
      <form onSubmit={e => { e.preventDefault(); doSend() }} className="p-3 flex items-center gap-2 border-t border-beige-200">
        <input
          value={text} onChange={e => setText(e.target.value)}
          placeholder={selectedDate
            ? `e.g. add a study session on ${selectedDate.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`
            : 'e.g. remind me to study at 3pm'}
          className="flex-1 rounded-full bg-white border border-beige-200 px-3 py-2 text-[12.5px] outline-none focus:border-amber"
        />
        <button type="submit" disabled={!text.trim() || busy}
          className="w-9 h-9 rounded-full bg-amber text-white flex items-center justify-center hover:opacity-90 disabled:opacity-50"
          aria-label="Send">
          <IconArrowUp />
        </button>
      </form>
    </div>
  )
}

function parseEventDate(e) {
  // Prefer date+time fields (YYYY-MM-DD + HH:MM), fall back to start/when/date strings.
  const d = e?.date
  if (d && d !== 'TBD' && /^\d{4}-\d{2}-\d{2}$/.test(d)) {
    const [y, m, day] = d.split('-').map(Number)
    const t = (e.time && e.time !== 'TBD' && /^\d{1,2}:\d{2}$/.test(e.time)) ? e.time.split(':').map(Number) : [0, 0]
    return new Date(y, m - 1, day, t[0], t[1])
  }
  const raw = e?.start || e?.when || e?.date
  if (!raw) return null
  const dt = new Date(raw)
  return isNaN(dt) ? null : dt
}

function sameDay(a, b) {
  if (!(a instanceof Date) || isNaN(a)) return false
  return a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth()
    && a.getDate() === b.getDate()
}
