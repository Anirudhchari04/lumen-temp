import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import TopStrip from '../components/TopStrip.jsx'
import MessageBubble from '../components/MessageBubble.jsx'
import LoadingDots from '../components/LoadingDots.jsx'
import { IconArrowUp, IconCode, IconCheck, IconRefresh, IconLock } from '../components/icons.jsx'
import { api } from '../lib/api.js'

export default function Agents({ user }) {
  const [agents, setAgents] = useState([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [active, setActive] = useState(null)
  const [params, setParams] = useSearchParams()
  const requestedId = params.get('agent')

  const name = user?.name?.split(' ')[0] || 'there'
  const initials = (user?.name || user?.email || 'U')
    .split(/[\s@._-]+/).filter(Boolean).slice(0, 2)
    .map(s => s[0]?.toUpperCase()).join('') || 'U'

  useEffect(() => {
    let cancelled = false
    api.agents()
      .then(list => { if (!cancelled) setAgents(Array.isArray(list) ? list : []) })
      .catch(e => { if (!cancelled) setErr(e?.message || 'Failed to load agents') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!requestedId || !agents.length) return
    const match = agents.find(a => a.id === requestedId)
    if (match) {
      setActive(match)
      const next = new URLSearchParams(params); next.delete('agent')
      setParams(next, { replace: true })
    }
  }, [requestedId, agents])

  return (
    <div className="flex-1 min-w-0 flex flex-col">
      <TopStrip userName={name} userInitials={initials} eventCount={agents.length} />
      <div className="px-6 pb-3 flex items-center justify-between">
        <div>
          <h2 className="text-[15px] font-medium text-ink">
            {active ? active.name : 'Agents on your network'}
          </h2>
          <p className="text-2xs text-ink-muted mt-0.5">
            {active
              ? 'Your Lumen keeps context across every agent you talk to.'
              : 'Each agent brings its own expertise. Pick one to start.'}
          </p>
        </div>
        {active && (
          <button
            onClick={() => setActive(null)}
            className="text-[12px] rounded-pill border border-beige-300 bg-beige-100 hover:bg-beige-200 text-ink-soft px-3 py-1"
          >
            ← All agents
          </button>
        )}
      </div>
      <div className="h-px bg-beige-200" />

      <div className="flex-1 min-h-0 overflow-hidden">
        {active
          ? <AgentWorkspace agent={active} key={active.id} />
          : <AgentGrid agents={agents} loading={loading} err={err} onPick={setActive} />
        }
      </div>
    </div>
  )
}

function AgentGrid({ agents, loading, err, onPick }) {
  if (loading) return <div className="p-8 flex items-center gap-3 text-ink-muted"><LoadingDots />Loading agents…</div>
  if (err) return <div className="p-8 text-[13px] text-[#b33a3a]">{err}</div>
  if (!agents.length) return <div className="p-8 text-[13px] text-ink-muted">No agents registered yet.</div>

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {agents.map(a => (
          <button
            key={a.id}
            onClick={() => onPick(a)}
            className="text-left rounded-card bg-white border border-beige-200 hover:border-amber-border p-4 flex flex-col gap-3 transition-colors"
          >
            <div className="flex items-start gap-3">
              <span
                aria-hidden
                className="w-10 h-10 rounded-inner flex items-center justify-center"
                style={{ backgroundColor: tintFor(a.id), color: strokeFor(a.id) }}
              >
                <IconCode size={18} />
              </span>
              <div className="flex-1 min-w-0">
                <div className="text-[14px] font-medium text-ink leading-tight">{a.name}</div>
                <div className="text-2xs text-ink-muted mt-0.5">{a.subject || a.protocol || 'agent'}</div>
              </div>
            </div>
            <p className="text-[12.5px] text-ink-soft leading-relaxed line-clamp-3">
              {a.description || 'No description.'}
            </p>
            <div className="flex items-center justify-between mt-auto pt-2 border-t border-beige-100">
              <span className="text-2xs text-ink-muted">{a.protocol || 'a2a/1.0'}</span>
              <span className="text-[12px] text-amber">Open →</span>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}

function AgentWorkspace({ agent }) {
  const [threadId, setThreadId] = useState(null)
  const [messages, setMessages] = useState([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [state, setState] = useState(null)
  const [threads, setThreads] = useState([])
  const [showTCs, setShowTCs] = useState(false)
  const endRef = useRef(null)

  const reloadState = async () => {
    try { setState(await api.lumenState(agent.id)) } catch {}
    try {
      const t = await api.threads(agent.id)
      setThreads(Array.isArray(t) ? t : [])
    } catch {}
  }

  useEffect(() => {
    setThreadId(null)
    setMessages([
      { role: 'agent', content: `You're chatting with ${agent.name}. ${agent.description || ''}`.trim() },
    ])
    reloadState()
     
  }, [agent.id])

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages.length, busy])

  const loadThread = async (tid) => {
    try {
      const t = await api.thread(tid)
      const msgs = (t?.messages || []).map(m => ({
        role: m.role === 'user' ? 'user' : 'agent',
        content: m.content,
      }))
      setMessages(msgs.length ? msgs : [{ role: 'agent', content: `(resuming ${t?.title || 'thread'})` }])
      setThreadId(tid)
    } catch (e) { console.error(e) }
  }

  const newThread = () => {
    setThreadId(null)
    setMessages([{ role: 'agent', content: `New session with ${agent.name}.` }])
  }

  const send = async (e) => {
    e?.preventDefault?.()
    const t = text.trim()
    if (!t || busy) return
    setMessages(m => [...m, { role: 'user', content: t }])
    setText(''); setBusy(true)
    try {
      const r = await api.taChat(agent.id, t, threadId)
      if (r?.thread_id) setThreadId(r.thread_id)
      setMessages(m => [...m, { role: 'agent', content: r?.reply || '…' }])
      reloadState()
    } catch {
      setMessages(m => [...m, { role: 'agent', content: "I couldn't reach the server — try again." }])
    } finally { setBusy(false) }
  }

  return (
    <div className="h-full grid grid-cols-1 lg:grid-cols-[1fr_320px]">
      <div className="h-full flex flex-col min-w-0">
        <div className="flex-1 min-h-0 overflow-y-auto px-6 py-4 flex flex-col gap-3">
          {messages.map((m, i) => (
            <MessageBubble key={i} role={m.role === 'user' ? 'user' : 'lumen'} content={m.content} />
          ))}
          {busy && <LoadingDots />}
          <div ref={endRef} />
        </div>
        <form onSubmit={send} className="px-6 pb-5 pt-1 flex items-center gap-2">
          <input
            value={text}
            onChange={e => setText(e.target.value)}
            placeholder={`Message ${agent.name}…`}
            className="flex-1 rounded-full bg-white border border-beige-200 px-4 py-2.5 text-[13.5px] outline-none focus:border-amber placeholder:text-ink-muted"
          />
          <button
            type="submit" disabled={!text.trim() || busy} aria-label="Send"
            className="w-10 h-10 rounded-full bg-amber text-white flex items-center justify-center hover:opacity-90 disabled:opacity-50"
          >
            <IconArrowUp />
          </button>
        </form>
      </div>

      <aside className="border-t lg:border-t-0 lg:border-l border-beige-200 bg-beige-50 flex flex-col overflow-y-auto">
        <ProgressSection agent={agent} state={state} />
        <TCSection state={state} open={showTCs} onToggle={() => setShowTCs(v => !v)} />
        <ThreadsSection
          threads={threads} activeId={threadId}
          onLoad={loadThread} onNew={newThread}
        />
      </aside>
    </div>
  )
}

function ProgressSection({ agent, state }) {
  const s = state?.current_ta_state || {}
  const level = s.current_level || 1
  const label = s.level_label || 'beginner'
  const sess = s.session_count || 0
  const covered = (s.topics_covered || []).length
  const mastered = (s.topics_mastered || []).length
  const module_ = s.current_module || '\u2014'
  const pct = covered > 0 ? Math.min(100, Math.round((mastered / covered) * 100)) : 0

  return (
    <div className="px-5 pt-5 pb-4 border-b border-beige-200">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-[13px] font-medium text-ink">{agent.name}</div>
          <div className="text-2xs text-ink-muted mt-0.5 capitalize">Level {level} \u00b7 {label}</div>
        </div>
        <span className="text-2xs text-ink-muted">{sess} sessions</span>
      </div>
      <div className="mt-3">
        <div className="flex justify-between text-2xs text-ink-muted mb-1">
          <span className="truncate">{module_}</span>
          <span>{pct}%</span>
        </div>
        <div className="h-[5px] rounded-full bg-beige-200 overflow-hidden">
          <div className="h-full bg-amber" style={{ width: `${pct}%` }} />
        </div>
      </div>
      <div className="mt-3 flex gap-2">
        <StatPill label="Topics covered" value={covered} />
        <StatPill label="Mastered" value={mastered} />
      </div>
    </div>
  )
}

function StatPill({ label, value }) {
  return (
    <div className="flex-1 rounded-inner bg-white border border-beige-200 px-2 py-1.5">
      <div className="text-[15px] font-medium text-ink leading-none">{value}</div>
      <div className="text-[10px] text-ink-muted mt-0.5">{label}</div>
    </div>
  )
}

function TCSection({ state, open, onToggle }) {
  const inv = state?.tc_inventory || { mastered: [], in_progress: [] }
  const mastered = inv.mastered || []
  const inProgress = inv.in_progress || []
  const total = mastered.length + inProgress.length

  return (
    <div className="border-b border-beige-200">
      <button
        onClick={onToggle}
        className="w-full px-5 py-3 flex items-center justify-between hover:bg-beige-100"
      >
        <div className="text-left">
          <div className="text-[13px] font-medium text-ink">Threshold concepts</div>
          <div className="text-2xs text-ink-muted mt-0.5">
            {total === 0 ? 'None unlocked yet' : `${mastered.length} mastered \u00b7 ${inProgress.length} in progress`}
          </div>
        </div>
        <span className="text-ink-muted text-[12px]">{open ? '\u25be' : '\u25b8'}</span>
      </button>
      {open && (
        <div className="px-5 pb-4 flex flex-col gap-2">
          {total === 0 && (
            <div className="text-[12px] text-ink-muted">
              Work with this agent \u2014 threshold concepts will unlock as you master them.
            </div>
          )}
          {mastered.map((tc, i) => <TCRow key={'m-' + i} tc={tc} status="done" />)}
          {inProgress.map((tc, i) => <TCRow key={'p-' + i} tc={tc} status="in-progress" />)}
        </div>
      )}
    </div>
  )
}

function TCRow({ tc, status }) {
  const label = typeof tc === 'string' ? tc : (tc.label || tc.id || 'concept')
  const detail = typeof tc === 'object' ? tc.detail : null
  return (
    <div className="flex items-start gap-2">
      <span className={`w-5 h-5 shrink-0 rounded-full flex items-center justify-center mt-0.5 ${
        status === 'done' ? 'bg-amber text-white'
        : status === 'in-progress' ? 'bg-white border border-amber-border text-amber'
        : 'bg-beige-200 text-ink-muted'
      }`}>
        {status === 'done' ? <IconCheck />
         : status === 'in-progress' ? <IconRefresh />
         : <IconLock />}
      </span>
      <div className="flex-1">
        <div className="text-[12.5px] text-ink leading-snug">{label}</div>
        {detail && <div className="text-2xs text-ink-muted mt-0.5">{detail}</div>}
      </div>
    </div>
  )
}

function ThreadsSection({ threads, activeId, onLoad, onNew }) {
  return (
    <div className="px-5 py-4">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[13px] font-medium text-ink">Chat history</div>
        <button onClick={onNew}
          className="text-[11px] rounded-pill bg-amber text-white px-2.5 py-0.5 hover:opacity-90">
          + New
        </button>
      </div>
      {!threads.length && (
        <div className="text-[12px] text-ink-muted">No past sessions yet.</div>
      )}
      <div className="flex flex-col gap-1">
        {threads.map(t => (
          <button
            key={t.id}
            onClick={() => onLoad(t.id)}
            className={`text-left rounded-inner px-2.5 py-1.5 border ${
              activeId === t.id
                ? 'bg-amber-light border-amber-border'
                : 'bg-white border-beige-200 hover:border-amber-border'
            }`}
          >
            <div className="text-[12.5px] text-ink truncate">{t.title || 'New chat'}</div>
            <div className="text-2xs text-ink-muted mt-0.5">
              {t.message_count || 0} msg{(t.message_count || 0) === 1 ? '' : 's'}
              {t.updated_at ? ' \u00b7 ' + relTime(t.updated_at) : ''}
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}

function relTime(iso) {
  try {
    const d = new Date(iso); const s = Math.floor((Date.now() - d.getTime()) / 1000)
    if (s < 60) return 'just now'
    if (s < 3600) return Math.floor(s/60) + 'm ago'
    if (s < 86400) return Math.floor(s/3600) + 'h ago'
    return Math.floor(s/86400) + 'd ago'
  } catch { return '' }
}

function tintFor(id) {
  if (id === 'math-ta')  return '#f0ebe0'
  if (id === 'cs-ta')    return '#e8eaf6'
  if (id === 'calendar') return '#e8f4ee'
  return '#fdf0e0'
}
function strokeFor(id) {
  if (id === 'math-ta')  return '#c8762a'
  if (id === 'cs-ta')    return '#5c6bc0'
  if (id === 'calendar') return '#4caf50'
  return '#c8762a'
}
