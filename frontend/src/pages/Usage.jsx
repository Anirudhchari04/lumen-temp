import { useEffect, useMemo, useState } from 'react'
import TopStrip from '../components/TopStrip.jsx'
import LoadingDots from '../components/LoadingDots.jsx'
import { api } from '../lib/api.js'

// Token-usage & cost dashboard. Reads /lumen/usage/tokens (per-user, with cost
// annotations) and /lumen/usage/tokens/all (admin, all users) and lets you
// compare per-agent token spend across session / today / week / lifetime.

const WINDOWS = [
  { id: 'session',  label: 'Session',  byKey: 'session_by_source',  totalKey: 'session',  note: 'Since the server last restarted' },
  { id: 'today',    label: 'Today',    byKey: 'today_by_source',    totalKey: 'today',    note: 'UTC day' },
  { id: 'week',     label: 'Week',     byKey: 'week_by_source',     totalKey: 'week',     note: 'Last 7 days' },
  { id: 'lifetime', label: 'Lifetime', byKey: 'lifetime_by_source', totalKey: 'lifetime', note: 'All time' },
]

// Per-agent accent colors so the same agent reads the same across views.
const AGENT_COLOR = {
  'lumen-chat': '#c8762a', 'lumen-router': '#9a6b3f', 'lumen': '#c8762a',
  notion: '#2f6f4f', drive: '#1a73e8', gmail: '#d93025', communication: '#7b3fa0',
  calendar: '#0b8043', arxiv: '#b3261e', wolfram: '#e8710a', social: '#1967d2',
  'coding-ta': '#3b3b6b', portfolio: '#6d4c41', shiksha: '#00796b',
}
const colorFor = (s) => AGENT_COLOR[s] || '#8a8a8a'

// Canonical subagent roster + the model each predominantly runs on (mirrors
// app/lumen/pricing.py SOURCE_MODEL). Used so every subagent shows a row in the
// per-agent table even when it has zero usage in the active window.
const AGENT_MODEL = {
  'lumen-chat': 'gpt-5.4', 'lumen-router': 'gpt-54-mini',
  notion: 'gpt-54-mini', drive: 'gpt-54-mini', gmail: 'gpt-54-mini',
  communication: 'gpt-54-mini', calendar: 'gpt-54-mini', arxiv: 'gpt-54-mini',
  social: 'gpt-54-mini', 'coding-ta': 'gpt-5.4',
}
const AGENT_ROSTER = Object.keys(AGENT_MODEL)
const fmtLatency = (ms) => (ms > 0 ? `${Math.round(ms).toLocaleString()}ms` : '—')

// Turn a spike event into a concrete "how to reduce this" suggestion.
function reduceTip(s) {
  const premium = (s.model || '').includes('5.4')
  const bigPrompt = (s.prompt || 0) > 2 * (s.completion || 1) && (s.prompt || 0) > 1500
  const bigOutput = (s.completion || 0) > 1500
  if (premium) return `Runs on the premium model — route ${s.source} to the mini tier if the task allows (~8× cheaper).`
  if (bigPrompt) return `Large prompt (${fmtInt(s.prompt)} tok in) — trim retrieved context or summarize before sending.`
  if (bigOutput) return `Long output (${fmtInt(s.completion)} tok out) — cap max tokens or ask for a shorter response.`
  return `Heavy single call — check whether ${s.source} can cache, batch, or shorten this request.`
}

const fmtInt = (n) => (n || 0).toLocaleString()
function fmtUsd(x) {
  const v = x || 0
  if (v === 0) return '$0'
  if (v >= 1) return '$' + v.toFixed(2)
  if (v < 0.01) return (v * 100).toFixed(3) + '¢'
  return '$' + v.toFixed(4)
}

export default function Usage({ user }) {
  const [usage, setUsage] = useState(null)
  const [all, setAll]     = useState(null)
  const [win, setWin]     = useState('lifetime')
  const [loading, setLoading] = useState(true)
  const [err, setErr]     = useState('')
  const [showAll, setShowAll] = useState(false)
  const [resetting, setResetting] = useState(false)

  const name = user?.name?.split(' ')[0] || 'there'
  const initials = (user?.name || user?.email || 'U')
    .split(/[\s@._-]+/).filter(Boolean).slice(0, 2).map(s => s[0]?.toUpperCase()).join('') || 'U'

  const load = async () => {
    setErr('')
    try {
      const u = await api.tokenUsage()
      setUsage(u)
    } catch (e) {
      setErr(e?.message || 'Failed to load usage')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const loadAll = async () => {
    if (all) return
    try { setAll(await api.tokenUsageAll()) }
    catch (e) { setErr(e?.message || 'Failed to load all-users view') }
  }

  const resetSession = async () => {
    setResetting(true)
    try { await api.resetTokenSession(); await load() }
    catch (e) { setErr(e?.message || 'Reset failed') }
    finally { setResetting(false) }
  }

  const winDef = WINDOWS.find(w => w.id === win)

  // Build sorted per-agent rows for the active window. Every known subagent gets
  // a row (zero usage included) so the roster is always complete.
  const rows = useMemo(() => {
    if (!usage) return []
    const bySrc = usage[winDef.byKey] || {}
    const seen = new Set()
    const list = Object.entries(bySrc).map(([source, c]) => {
      seen.add(source)
      return {
        source,
        model: AGENT_MODEL[source] || c.model || '',
        total: c.total || 0,
        prompt: c.prompt || 0,
        completion: c.completion || 0,
        calls: c.calls || 0,
        cost: c.cost_usd || 0,
        avgLatency: c.avg_latency_ms || 0,
      }
    })
    for (const s of AGENT_ROSTER) {
      if (!seen.has(s)) {
        list.push({ source: s, model: AGENT_MODEL[s], total: 0, prompt: 0,
                    completion: 0, calls: 0, cost: 0, avgLatency: 0 })
      }
    }
    // Active agents first (by tokens), then idle ones alphabetically.
    list.sort((a, b) => b.total - a.total || a.source.localeCompare(b.source))
    return list
  }, [usage, win])

  const totals = useMemo(() => {
    const t = rows.reduce((acc, r) => ({
      total: acc.total + r.total, calls: acc.calls + r.calls, cost: acc.cost + r.cost,
      prompt: acc.prompt + r.prompt, completion: acc.completion + r.completion,
      latWeighted: acc.latWeighted + r.avgLatency * r.calls,
    }), { total: 0, calls: 0, cost: 0, prompt: 0, completion: 0, latWeighted: 0 })
    t.avgLatency = t.calls ? Math.round(t.latWeighted / t.calls) : 0
    return t
  }, [rows])

  const maxTotal = rows.length ? rows[0].total : 0

  const spikes = usage?.spikes || []
  const recs = usage?.recommendations || null

  return (
    <div className="flex-1 min-w-0 flex flex-col">
      <TopStrip userName={name} userInitials={initials} />
      <div className="h-px bg-gradient-to-r from-transparent via-beige-200 to-transparent shrink-0" />

      <div className="px-6 pt-5 pb-4 shrink-0" style={{ borderBottom: '0.5px solid rgba(0,0,0,0.07)' }}>
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div>
            <h2 className="text-[18px] font-semibold text-ink tracking-tight">Token usage & cost</h2>
            <p className="text-[12px] text-ink-muted mt-1">
              Per-agent LLM token spend. Cost is estimated from model pricing — compare relative spend to optimise.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={load}
              className="text-[12px] px-3 py-1.5 rounded-pill bg-beige-100 border border-beige-300 hover:bg-beige-200 text-ink-soft">
              Refresh
            </button>
            {win === 'session' && (
              <button onClick={resetSession} disabled={resetting}
                className="text-[12px] px-3 py-1.5 rounded-pill border border-rose-300 text-rose-600 hover:bg-rose-50 disabled:opacity-50">
                {resetting ? 'Resetting…' : 'Reset session'}
              </button>
            )}
          </div>
        </div>

        {/* Window tabs */}
        <div className="flex items-center gap-1.5 mt-3">
          {WINDOWS.map(w => (
            <button key={w.id} onClick={() => setWin(w.id)}
              className={[
                'text-[12px] px-3 py-1.5 rounded-pill border transition-colors',
                win === w.id
                  ? 'bg-amber text-white border-amber'
                  : 'bg-beige-50 border-beige-200 text-ink-soft hover:bg-beige-100',
              ].join(' ')}>
              {w.label}
            </button>
          ))}
          <span className="text-[11px] text-ink-muted ml-1">{winDef.note}</span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        {loading && <LoadingDots label="Loading usage" />}
        {err && <div className="text-[13px] text-rose-600 mb-4">{err}</div>}

        {!loading && usage && (
          <>
            {/* Summary cards */}
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">
              <SummaryCard label="Total tokens" value={fmtInt(totals.total)} />
              <SummaryCard label="LLM calls" value={fmtInt(totals.calls)} />
              <SummaryCard label="Est. cost" value={fmtUsd(totals.cost)} accent />
              <SummaryCard label="Avg latency"
                value={totals.avgLatency ? `${fmtInt(totals.avgLatency)}ms` : '—'} small />
              <SummaryCard label="Prompt : Completion"
                value={`${fmtInt(totals.prompt)} : ${fmtInt(totals.completion)}`} small />
            </div>

            {/* Where cost goes & how to reduce it (advisory only) */}
            {win === 'lifetime' && recs && recs.items?.length > 0 && (
              <div className="rounded-card bg-white border border-beige-200 overflow-hidden mb-6">
                <div className="px-4 py-3 border-b border-beige-200 flex items-center justify-between gap-3 flex-wrap">
                  <h3 className="text-[13px] font-medium text-ink">Where your cost goes · how to reduce it</h3>
                  {recs.potential_savings_usd > 0 && (
                    <span className="text-[11px] px-2 py-1 rounded-pill bg-emerald-50 border border-emerald-200 text-emerald-700">
                      Up to {fmtUsd(recs.potential_savings_usd)} reducible
                    </span>
                  )}
                </div>
                <div className="divide-y divide-beige-100">
                  {recs.items.filter(r => r.current_cost_usd > 0).map(r => (
                    <div key={r.source} className="px-4 py-3">
                      <div className="flex items-center justify-between gap-3 mb-1">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="w-2 h-2 rounded-full shrink-0" style={{ background: colorFor(r.source) }} />
                          <span className="text-[12.5px] font-medium text-ink truncate">{r.source}</span>
                          <span className="text-[10.5px] text-ink-muted shrink-0">{r.current_model}</span>
                          {r.on_cheapest && (
                            <span className="text-[9.5px] px-1.5 py-0.5 rounded-pill bg-beige-100 text-ink-muted shrink-0">cheapest tier</span>
                          )}
                        </div>
                        <div className="flex items-center gap-3 shrink-0 text-right tabular-nums">
                          <span className="text-[11px] text-ink-muted">{r.share_pct}% of cost</span>
                          <span className="text-[12px] text-ink" style={{ minWidth: 56, display: 'inline-block' }}>{fmtUsd(r.current_cost_usd)}</span>
                        </div>
                      </div>
                      {/* Cost share bar */}
                      <div className="h-1.5 rounded-full bg-beige-100 overflow-hidden mb-1.5">
                        <div className="h-full rounded-full" style={{ width: `${Math.min(r.share_pct, 100)}%`, background: colorFor(r.source), opacity: 0.85 }} />
                      </div>
                      {r.advice && <div className="text-[11.5px] text-ink-soft leading-snug">{r.advice}</div>}
                      {r.suggested_model && r.savings_usd > 0 && (
                        <div className="text-[11px] mt-1 flex items-center gap-1.5 flex-wrap">
                          <span className="px-1.5 py-0.5 rounded-pill bg-emerald-50 border border-emerald-200 text-emerald-700 tabular-nums">
                            → {r.suggested_model}: save {fmtUsd(r.savings_usd)} ({r.savings_pct}%)
                          </span>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
                <div className="px-4 py-2.5 border-t border-beige-100 text-[10.5px] text-ink-muted">
                  Estimates only — Lumen never switches models for you. Compare relative spend and decide what to move.
                </div>
              </div>
            )}

            {/* Cost spikes — expensive single calls worth investigating */}
            {spikes.length > 0 && (
              <div className="rounded-card border overflow-hidden mb-6"
                   style={{ background: 'rgba(217,48,37,0.04)', borderColor: 'rgba(217,48,37,0.25)' }}>
                <div className="px-4 py-3 border-b flex items-center justify-between"
                     style={{ borderColor: 'rgba(217,48,37,0.18)' }}>
                  <h3 className="text-[13px] font-medium text-rose-700 flex items-center gap-1.5">
                    <span>💸</span> Cost spikes · most expensive recent calls
                  </h3>
                  <span className="text-[11px] text-ink-muted">{spikes.length} flagged</span>
                </div>
                <div className="divide-y" style={{ borderColor: 'rgba(217,48,37,0.10)' }}>
                  {spikes.map(s => (
                    <div key={s.id} className="px-4 py-2.5">
                      <div className="flex items-center justify-between gap-3">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="w-2 h-2 rounded-full shrink-0" style={{ background: colorFor(s.source) }} />
                          <span className="text-[12.5px] font-medium text-ink truncate">{s.source}</span>
                          <span className="text-[10.5px] text-ink-muted shrink-0">{fmtInt(s.total)} tok · {s.model}</span>
                        </div>
                        <span className="text-[12.5px] font-semibold text-rose-700 tabular-nums shrink-0">{fmtUsd(s.cost_usd)}</span>
                      </div>
                      <div className="text-[11px] text-ink-soft mt-0.5">{s.reason}</div>
                      <div className="text-[11px] mt-1" style={{ color: '#9a3412' }}>↳ {reduceTip(s)}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Per-agent table */}
            <div className="rounded-card bg-white border border-beige-200 overflow-hidden">
              <div className="px-4 py-3 border-b border-beige-200 flex items-center justify-between">
                <h3 className="text-[13px] font-medium text-ink">Every subagent · {winDef.label}</h3>
                <span className="text-[11px] text-ink-muted">
                  {rows.filter(r => r.calls > 0).length} active / {rows.length} total
                </span>
              </div>

              {/* Column headers: tokens · performance · cost */}
              <div className="px-4 py-1.5 border-b border-beige-100 flex items-center gap-3 text-[10px] uppercase tracking-wide text-ink-muted">
                <span className="flex-1">Agent · model</span>
                <span className="w-[70px] text-right">Tokens</span>
                <span className="w-[70px] text-right">Avg lat</span>
                <span className="w-[64px] text-right">Cost</span>
              </div>

              <div className="divide-y divide-beige-100">
                {rows.map(r => {
                  const pctOfMax = maxTotal ? (r.total / maxTotal) * 100 : 0
                  const pctCost = totals.cost ? (r.cost / totals.cost) * 100 : 0
                  const idle = r.calls === 0
                  const premium = (r.model || '').includes('5.4')
                  return (
                    <div key={r.source} className={`px-4 py-2.5 ${idle ? 'opacity-45' : ''}`}>
                      <div className="flex items-center justify-between gap-3 mb-1.5">
                        <div className="flex items-center gap-2 min-w-0 flex-1">
                          <span className="w-2 h-2 rounded-full shrink-0" style={{ background: colorFor(r.source) }} />
                          <span className="text-[12.5px] font-medium text-ink truncate">{r.source}</span>
                          {r.model && (
                            <span className={`text-[9.5px] px-1.5 py-0.5 rounded-pill shrink-0 ${premium ? 'bg-amber-50 text-amber-700 border border-amber-200' : 'bg-beige-100 text-ink-muted'}`}>
                              {r.model}
                            </span>
                          )}
                          <span className="text-[10.5px] text-ink-muted shrink-0">{r.calls} call{r.calls === 1 ? '' : 's'}</span>
                        </div>
                        <div className="flex items-center shrink-0 text-right tabular-nums">
                          <span className="w-[70px] text-[12px] text-ink">{idle ? '—' : fmtInt(r.total)}</span>
                          <span className="w-[70px] text-[12px] text-ink-soft">{fmtLatency(r.avgLatency)}</span>
                          <span className="w-[64px] text-[12px] font-medium" style={{ color: idle ? '#aaa' : colorFor(r.source) }}>
                            {idle ? '$0' : fmtUsd(r.cost)}
                          </span>
                        </div>
                      </div>
                      <div className="h-1.5 rounded-full bg-beige-100 overflow-hidden">
                        <div className="h-full rounded-full" style={{ width: `${pctOfMax}%`, background: colorFor(r.source), opacity: 0.85 }} />
                      </div>
                      {!idle && (
                        <div className="flex items-center gap-3 mt-1 text-[10.5px] text-ink-muted tabular-nums">
                          <span>prompt {fmtInt(r.prompt)}</span>
                          <span>·</span>
                          <span>completion {fmtInt(r.completion)}</span>
                          <span>·</span>
                          <span>{pctCost.toFixed(1)}% of cost</span>
                          <span>·</span>
                          <span>{r.calls ? fmtInt(Math.round(r.total / r.calls)) : 0} tok/call</span>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>

            {/* Admin — all users */}
            <div className="mt-6">
              <button
                onClick={() => { setShowAll(s => !s); if (!showAll) loadAll() }}
                className="text-[12px] px-3 py-1.5 rounded-pill bg-beige-100 border border-beige-300 hover:bg-beige-200 text-ink-soft">
                {showAll ? 'Hide' : 'Show'} all-users comparison (admin)
              </button>

              {showAll && (
                <div className="mt-3 rounded-card bg-white border border-beige-200 overflow-hidden">
                  {!all ? (
                    <div className="px-4 py-6"><LoadingDots label="Loading all users" /></div>
                  ) : (
                    <>
                      <div className="px-4 py-3 border-b border-beige-200 flex items-center justify-between">
                        <h3 className="text-[13px] font-medium text-ink">All users · lifetime</h3>
                        <span className="text-[12px] text-ink-soft tabular-nums">
                          {fmtInt(all.aggregate_total_tokens)} tokens · {fmtUsd(all.aggregate_cost_usd)}
                        </span>
                      </div>

                      {/* Aggregate by agent */}
                      <div className="px-4 py-3 border-b border-beige-100">
                        <div className="text-[11px] font-medium text-ink-muted mb-2">Aggregate by agent</div>
                        <div className="flex flex-wrap gap-1.5">
                          {Object.entries(all.aggregate_by_source || {})
                            .sort((a, b) => (b[1].total || 0) - (a[1].total || 0))
                            .map(([src, c]) => (
                              <span key={src} className="inline-flex items-center gap-1.5 text-[11px] px-2 py-1 rounded-pill bg-beige-50 border border-beige-200">
                                <span className="w-1.5 h-1.5 rounded-full" style={{ background: colorFor(src) }} />
                                <span className="text-ink">{src}</span>
                                <span className="text-ink-muted tabular-nums">{fmtInt(c.total)} · {fmtUsd(c.cost_usd)}</span>
                              </span>
                            ))}
                        </div>
                      </div>

                      {/* Per user */}
                      <div className="divide-y divide-beige-100">
                        {(all.users || [])
                          .sort((a, b) => (b.lifetime_total || 0) - (a.lifetime_total || 0))
                          .map(u => (
                            <div key={u.user_id} className="px-4 py-2.5 flex items-center justify-between gap-3">
                              <div className="min-w-0">
                                <div className="text-[12.5px] text-ink truncate">{u.name || u.user_id}</div>
                                <div className="text-[10.5px] text-ink-muted">{u.lifetime_calls} calls</div>
                              </div>
                              <div className="text-right tabular-nums shrink-0">
                                <div className="text-[12px] text-ink">{fmtInt(u.lifetime_total)}</div>
                                <div className="text-[11px] text-amber-700">{fmtUsd(u.lifetime_cost_usd)}</div>
                              </div>
                            </div>
                          ))}
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>

            <p className="text-[10.5px] text-ink-muted mt-4">
              Cost is an estimate: each agent is priced against its predominant model (mini vs full).
              Set <code>LUMEN_PRICING</code> for contracted rates. Run <code>scripts/benchmark_agents.py</code> to
              compare token usage, latency and cost across agents.
            </p>
          </>
        )}
      </div>
    </div>
  )
}

function SummaryCard({ label, value, accent, small }) {
  return (
    <div className="rounded-card bg-white border border-beige-200 px-4 py-3">
      <div className="text-[10.5px] text-ink-muted uppercase tracking-wide">{label}</div>
      <div className={[
        small ? 'text-[14px]' : 'text-[20px]',
        'font-semibold mt-0.5 tabular-nums',
        accent ? 'text-amber-700' : 'text-ink',
      ].join(' ')}>
        {value}
      </div>
    </div>
  )
}
