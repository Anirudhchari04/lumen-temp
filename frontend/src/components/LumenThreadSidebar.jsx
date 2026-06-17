/**
 * LumenThreadSidebar — shows conversation thread history for Lumen chat.
 * Clicking a thread loads its messages. New Chat button starts a fresh thread.
 */

import { useEffect, useState } from 'react'
import { api } from '../lib/api.js'

export default function LumenThreadSidebar({ activeThreadId, onSelectThread, onNewThread }) {
  const [threads, setThreads] = useState([])
  const [loading, setLoading] = useState(true)
  const [usage, setUsage] = useState(null)
  const [usageOpen, setUsageOpen] = useState(false)

  useEffect(() => {
    api.threads('lumen')
      .then(ts => setThreads(Array.isArray(ts) ? ts : []))
      .catch(() => setThreads([]))
      .finally(() => setLoading(false))
  }, [activeThreadId]) // reload when thread changes (after new messages)

  // Poll token usage every 15s, and once on mount/threadchange
  useEffect(() => {
    const fetchUsage = async () => {
      const token = localStorage.getItem('lumen.token')
      if (!token) return
      try {
        const r = await fetch('/lumen/usage/tokens', { headers: { Authorization: `Bearer ${token}` } })
        if (r.ok) setUsage(await r.json())
      } catch {}
    }
    fetchUsage()
    const id = setInterval(fetchUsage, 15000)
    return () => clearInterval(id)
  }, [activeThreadId])

  const fmtTokens = (n) => {
    n = n || 0
    if (n < 1000) return String(n)
    if (n < 1_000_000) return (n / 1000).toFixed(1) + 'k'
    return (n / 1_000_000).toFixed(2) + 'M'
  }

  const formatDate = (iso) => {
    if (!iso) return ''
    try {
      const d = new Date(iso)
      const now = new Date()
      const diffDays = Math.floor((now - d) / 86400000)
      if (diffDays === 0) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      if (diffDays === 1) return 'Yesterday'
      if (diffDays < 7) return d.toLocaleDateString([], { weekday: 'short' })
      return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
    } catch { return '' }
  }

  return (
    <aside className="w-56 flex-shrink-0 bg-beige-50 border-r border-beige-200 flex flex-col h-full min-h-0 overflow-hidden">
      {/* Header */}
      <div className="px-3 py-3 border-b border-beige-200 flex items-center justify-between shrink-0">
        <span className="text-[11px] font-semibold text-ink-soft uppercase tracking-wide">History</span>
        <button
          onClick={onNewThread}
          title="New conversation"
          className="text-amber hover:text-amber-600 text-lg leading-none"
        >
          ✏️
        </button>
      </div>

      {/* Thread list — scrolls within its allotted space */}
      <div className="flex-1 min-h-0 overflow-y-auto py-1">
        {loading && (
          <div className="px-3 py-4 text-[11px] text-ink-muted">Loading…</div>
        )}
        {!loading && threads.length === 0 && (
          <div className="px-3 py-4 text-[11px] text-ink-muted">No conversations yet.</div>
        )}
        {threads.map(t => (
          <button
            key={t.id}
            onClick={() => onSelectThread(t.id, t.title)}
            className={`w-full text-left px-3 py-2.5 transition-colors ${
              t.id === activeThreadId
                ? 'bg-amber/10 border-r-2 border-amber'
                : 'hover:bg-beige-100'
            }`}
          >
            <div className="text-[12px] font-medium text-ink-base truncate">
              {t.title || 'New Chat'}
            </div>
            <div className="text-[10px] text-ink-muted mt-0.5 flex gap-2">
              <span>{formatDate(t.updated_at)}</span>
              {t.message_count > 0 && <span>{t.message_count} msgs</span>}
            </div>
          </button>
        ))}
      </div>

      {/* Token usage chip — PINNED at the bottom (shrink-0 so flex never collapses it) */}
      <div className="shrink-0 border-t border-beige-200 px-3 py-2 bg-beige-50">
        <button
          type="button"
          onClick={() => setUsageOpen(o => !o)}
          className="w-full text-left flex items-center gap-1.5 hover:bg-beige-100 rounded px-1 py-1"
          title="Tokens used by Lumen's main LLM only — sub-agents (Gmail, Drive, Notion, Calendar) are excluded"
        >
          <span className="text-[10px] text-ink-muted">⚡ Tokens</span>
          <span className="text-[11px] font-medium text-ink-base">
            {usage ? fmtTokens(usage.session?.total) : '—'}
          </span>
          <span className="text-[10px] text-ink-muted ml-auto">
            {usageOpen ? '▾' : '▸'}
          </span>
        </button>
        {usageOpen && usage && (
          <div className="mt-1.5 px-1 text-[10px] text-ink-soft space-y-0.5">
            <div className="flex justify-between">
              <span>Session</span>
              <span className="font-mono">{fmtTokens(usage.session?.total)} ({usage.session?.calls || 0} calls)</span>
            </div>
            <div className="flex justify-between">
              <span>Today</span>
              <span className="font-mono">{fmtTokens(usage.today?.total)}</span>
            </div>
            <div className="flex justify-between">
              <span>Last 7 days</span>
              <span className="font-mono">{fmtTokens(usage.week?.total)}</span>
            </div>
            <div className="flex justify-between font-medium text-ink-base">
              <span>Lifetime</span>
              <span className="font-mono">{fmtTokens(usage.lifetime?.total)}</span>
            </div>
            <button
              type="button"
              onClick={async () => {
                const token = localStorage.getItem('lumen.token')
                await fetch('/lumen/usage/tokens/reset-session', {
                  method: 'POST', headers: { Authorization: `Bearer ${token}` }
                })
                // Refresh
                const r = await fetch('/lumen/usage/tokens', { headers: { Authorization: `Bearer ${token}` } })
                if (r.ok) setUsage(await r.json())
              }}
              className="mt-1 text-[10px] text-ink-muted underline hover:text-ink-soft"
            >
              Reset session
            </button>
            <div className="text-2xs text-ink-muted pt-1">
              Sub-agents (Gmail, Drive, Notion, Calendar) excluded.
            </div>
          </div>
        )}
      </div>
    </aside>
  )
}
