import { useEffect, useState } from 'react'
import { api } from '../lib/api.js'

const SHIKSHA_FRONTEND = 'https://ekalaiva-frontend-app-cncbg2hwdueedpfn.westus2-01.azurewebsites.net'

function TACard({ agent, progressMap }) {
  const p = progressMap[agent.agent_id]
  const lastActive = p?.last_active
    ? new Date(p.last_active).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })
    : null
  const continueUrl = p?.continue_url || agent.url

  return (
    <div className="glass-card rounded-xl p-4 flex flex-col gap-3 transition-all duration-200 hover:shadow-md">
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <h3 className="text-[13.5px] font-semibold text-ink leading-snug">{agent.name}</h3>
          {agent.subject && (
            <p className="text-[11.5px] text-ink-muted mt-0.5">{agent.subject}</p>
          )}
        </div>
        {p && (
          <span className="text-[10px] font-medium text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-full border border-emerald-100 shrink-0">
            Active
          </span>
        )}
      </div>

      {p && (
        <div className="flex gap-4 text-[11.5px] text-ink-muted">
          <span>{p.thread_count} session{p.thread_count !== 1 ? 's' : ''}</span>
          {lastActive && <span>Last active {lastActive}</span>}
        </div>
      )}

      <a
        href={continueUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="mt-auto text-center text-[12.5px] font-medium px-3 py-2 rounded-xl bg-amber text-white hover:bg-amber/90 active:scale-[0.98] transition-all duration-150"
      >
        {p ? 'Continue on Shiksha' : 'Open on Shiksha'}
      </a>
    </div>
  )
}

export default function TAPanel() {
  const [agents, setAgents]     = useState([])
  const [progress, setProgress] = useState([])
  const [loading, setLoading]   = useState(true)
  const [err, setErr]           = useState('')

  useEffect(() => {
    setLoading(true)
    Promise.all([
      api.shikshaAgents().catch(() => ({ agents: [] })),
      api.shikshaProgress().catch(() => ({ progress: [] })),
    ]).then(([agR, prR]) => {
      setAgents(agR?.agents || [])
      setProgress(prR?.progress || [])
    }).catch(e => setErr(e?.message || 'Failed to load')).finally(() => setLoading(false))
  }, [])

  const progressMap = Object.fromEntries(progress.map(p => [p.agent_id, p]))

  return (
    <div className="flex-1 flex flex-col overflow-hidden" style={{ background: 'transparent' }}>
      {/* Page header */}
      <div className="px-6 pt-6 pb-4 shrink-0" style={{ borderBottom: '0.5px solid rgba(0,0,0,0.07)' }}>
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-[18px] font-semibold text-ink tracking-tight">My Courses</h1>
            <p className="text-[12px] text-ink-muted mt-1">
              Your active Shiksha courses — continue where you left off
            </p>
          </div>
          <a
            href={SHIKSHA_FRONTEND}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[12.5px] font-medium text-amber hover:text-amber/80 flex items-center gap-1 transition-colors"
          >
            Open Shiksha
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden>
              <path d="M2 10L10 2M10 2H5M10 2v5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
          </a>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-6 py-5">
        {loading && (
          <div className="text-[13px] text-ink-muted py-10 text-center">Loading courses…</div>
        )}
        {err && (
          <div className="text-[12.5px] text-red-600 bg-red-50 rounded-xl px-4 py-3 border border-red-100">{err}</div>
        )}

        {!loading && !err && (
          agents.length > 0 ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {agents.map(a => (
                <TACard key={a.agent_id} agent={a} progressMap={progressMap} />
              ))}
            </div>
          ) : (
            <div className="text-center py-16">
              <div
                className="w-14 h-14 rounded-2xl bg-amber/10 flex items-center justify-center mx-auto mb-4"
              >
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#c8892a" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 3L2 9l10 6 10-6-10-6z" />
                  <path d="M6 12v5c0 1.1 2.7 3 6 3s6-1.9 6-3v-5" />
                </svg>
              </div>
              <p className="text-[14px] font-medium text-ink mb-1">No active courses yet</p>
              <p className="text-[12.5px] text-ink-muted mb-4">Start learning on Shiksha and your courses will appear here</p>
              <a
                href={SHIKSHA_FRONTEND}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 text-[12.5px] font-medium text-amber hover:text-amber/80 transition-colors"
              >
                Browse courses on Shiksha
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                  <path d="M2 10L10 2M10 2H5M10 2v5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
                </svg>
              </a>
            </div>
          )
        )}
      </div>
    </div>
  )
}
