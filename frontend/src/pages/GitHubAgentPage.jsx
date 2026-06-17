/**
 * GitHub Agent — full UI page.
 * Replaces the old Portfolio page. Tabs: Portfolio · Repos · Commits · Branches · PRs · Classroom
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../lib/api.js'
import LumenChat from '../components/LumenChat.jsx'
import LumenThreadSidebar from '../components/LumenThreadSidebar.jsx'

// ── helpers ────────────────────────────────────────────────────────────────
const FOLDERS = ['general', 'coding-ta', 'math-ta', 'cs-ta', 'science-ta', 'english-ta', 'social-ta']

function FileIcon({ name }) {
  const ext = name?.split('.').pop()?.toLowerCase()
  if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg'].includes(ext)) return <span>🖼️</span>
  if (['mp4', 'mov', 'avi', 'webm'].includes(ext)) return <span>🎬</span>
  if (['pdf'].includes(ext)) return <span>📄</span>
  return <span>📎</span>
}

function StatusDot({ conclusion, status }) {
  if (conclusion === 'success') return <span className="text-emerald-500">✅</span>
  if (conclusion === 'failure') return <span className="text-rose-500">❌</span>
  if (status === 'in_progress' || status === 'queued') return <span className="text-amber-500">🟡</span>
  return <span className="text-gray-400">⚪</span>
}

const TABS = ['Portfolio', 'Repos', 'Commits', 'Branches', 'Pull Requests', 'Classroom', 'Chat']

// ── main component ─────────────────────────────────────────────────────────
export default function GitHubAgentPage({ session }) {
  const { lumenMessages, sendLumen, lumenTyping, confirmProposal,
          lumenThreadId, loadThread, newThread, onSpeakEndRef } = session

  const [tab, setTab]           = useState('Portfolio')
  const [status, setStatus]     = useState(null)
  const [portPath, setPortPath] = useState('')
  const [files, setFiles]       = useState([])
  const [staged, setStaged]     = useState([])
  const [runs, setRuns]         = useState([])
  const [loading, setLoading]   = useState(false)
  const [uploading, setUploading] = useState(false)
  const [committing, setCommitting] = useState(false)
  const [taHint, setTaHint]     = useState('general')
  const [err, setErr]           = useState('')
  const fileRef                 = useRef(null)

  // — repos / commits / branches / PRs state —
  const [repos, setRepos]           = useState([])
  const [selectedRepo, setSelectedRepo] = useState('')
  const [commits, setCommits]       = useState([])
  const [branches, setBranches]     = useState([])
  const [prs, setPrs]               = useState([])
  const [dataLoading, setDataLoading] = useState(false)

  // — classroom state —
  const [classrooms, setClassrooms] = useState([])
  const [selectedClassroom, setSelectedClassroom] = useState(null)
  const [assignments, setAssignments] = useState([])
  const [classLoading, setClassLoading] = useState(false)

  // — sidebar —
  const [showSidebar, setShowSidebar] = useState(false)

  const greeted = useRef(false)

  // ── portfolio loader ──────────────────────────────────────────────────────
  const loadStatus = useCallback(async () => {
    try { setStatus(await api.portfolioStatus()) } catch { setStatus(null) }
  }, [])

  const loadFiles = useCallback(async (p = '') => {
    setLoading(true); setErr('')
    try { const r = await api.portfolioFiles(p); setFiles(r?.files || []); setPortPath(p) }
    catch (e) { setErr(e?.message || 'Failed') }
    finally { setLoading(false) }
  }, [])

  const loadStaged = useCallback(async () => {
    try { const r = await api.portfolioStaged(); setStaged(r?.staged || []) }
    catch { /* ignore */ }
  }, [])

  const loadRuns = useCallback(async () => {
    try { const r = await api.portfolioActions(5); setRuns(r?.runs || []) }
    catch { setRuns([]) }
  }, [])

  useEffect(() => { loadStatus() }, [loadStatus])

  useEffect(() => {
    if (status?.repo_exists) { loadFiles(''); loadStaged(); loadRuns() }
  }, [status?.repo_exists, loadFiles, loadStaged, loadRuns])

  // ── portfolio actions ─────────────────────────────────────────────────────
  const handleStage = async (file) => {
    if (!file) return
    setUploading(true); setErr('')
    try { const r = await api.portfolioStage(file, taHint); setStaged(r?.all_staged || []) }
    catch (e) { setErr(e?.message || 'Staging failed') }
    finally { setUploading(false) }
  }

  const handleCommit = async () => {
    if (!staged.length) return
    setCommitting(true); setErr('')
    try {
      const r = await api.portfolioCommit()
      setStaged(r?.failed?.map(f => ({ path: f.path, filename: f.path.split('/').pop() })) || [])
      await loadStaged(); await loadFiles(portPath); loadRuns()
      if (r?.failed?.length) setErr(`Committed ${r.committed?.length || 0}, failed ${r.failed.length}.`)
    } catch (e) { setErr(e?.message || 'Commit failed') }
    finally { setCommitting(false) }
  }

  const handleUnstage = async (id) => {
    try { const r = await api.portfolioUnstage(id); setStaged(r?.staged || []) }
    catch (e) { setErr(e?.message || 'Failed') }
  }

  const handleDelete = async (filePath) => {
    if (!confirm(`Delete ${filePath}?`)) return
    try { await api.portfolioDelete(filePath); await loadFiles(portPath) }
    catch (e) { setErr(e?.message || 'Delete failed') }
  }

  // ── repos / commits / branches / PRs ─────────────────────────────────────
  const loadRepos = useCallback(async () => {
    setDataLoading(true)
    try {
      const r = await api.lumenChat('list my repos', null)
      // Extract repo list from reply if structured, else just show the reply
      setRepos(r?.repos || [])
      if (!r?.repos) {
        // Fallback: send to chat tab
        sendLumen('List my GitHub repos', { fromVoice: false })
        setTab('Chat')
      }
    } catch { setRepos([]) }
    finally { setDataLoading(false) }
  }, [sendLumen])

  const loadRepoData = useCallback(async (repo) => {
    if (!repo) return
    setDataLoading(true)
    try {
      const [cmts, brs, prList] = await Promise.all([
        api.authed ? null : null,
        null, null,
      ])
      // Send to github agent and switch to chat
      sendLumen(`Show commits for ${repo}`, { fromVoice: false })
    } catch { /* ignore */ }
    finally { setDataLoading(false) }
  }, [sendLumen])

  // ── classroom ─────────────────────────────────────────────────────────────
  const loadClassrooms = useCallback(async () => {
    setClassLoading(true)
    try {
      sendLumen('List my GitHub Classrooms', { fromVoice: false })
      setTab('Chat')
    } catch { /* ignore */ }
    finally { setClassLoading(false) }
  }, [sendLumen])

  // ── tab activation ────────────────────────────────────────────────────────
  useEffect(() => {
    if (tab === 'Repos') {
      sendLumen('List my GitHub repos', { fromVoice: false })
      setTab('Chat')
    } else if (tab === 'Commits') {
      sendLumen('Show my recent commits', { fromVoice: false })
      setTab('Chat')
    } else if (tab === 'Branches') {
      sendLumen('List my branches', { fromVoice: false })
      setTab('Chat')
    } else if (tab === 'Pull Requests') {
      sendLumen('Show my pull requests', { fromVoice: false })
      setTab('Chat')
    } else if (tab === 'Classroom') {
      sendLumen('List my GitHub Classrooms and assignments', { fromVoice: false })
      setTab('Chat')
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab])

  // ── auto-greet ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (greeted.current) return
    greeted.current = true
    if (lumenMessages.length === 0) {
      sendLumen('Show my GitHub portfolio', { fromVoice: false })
    }
  }, []) // eslint-disable-line

  // ── render helpers ────────────────────────────────────────────────────────
  const CHAT_CHIPS = [
    'Show my GitHub portfolio',
    'List my recent commits',
    'Any rebases on main?',
    'Show my pull requests',
    'List my repos',
    'Show my recent GitHub actions',
    'List my GitHub Classrooms',
  ]

  const PortfolioTab = () => {
    if (!status) return <div className="p-6 text-[13px] text-ink-muted">Loading…</div>

    if (!status.connected) return (
      <div className="flex flex-col items-center justify-center gap-3 p-8 flex-1">
        <div className="text-[40px]">🔗</div>
        <div className="text-[15px] font-medium text-ink">GitHub not connected</div>
        <div className="text-[13px] text-ink-muted text-center max-w-xs">
          Open your <strong>Profile</strong> (top-right) and use <strong>Connect GitHub</strong>.
        </div>
      </div>
    )

    if (!status.repo_exists) return (
      <div className="flex flex-col items-center justify-center gap-3 p-8 flex-1">
        <div className="text-[40px]">📦</div>
        <div className="text-[15px] font-medium text-ink">Portfolio repo not initialized</div>
        <button onClick={() => api.portfolioInit().then(loadStatus)}
          className="text-[13px] px-4 py-2 rounded-full bg-gray-800 text-white hover:bg-gray-700">
          Create portfolio repo
        </button>
      </div>
    )

    const folders = files.filter(f => f.type === 'dir')
    const fileItems = files.filter(f => f.type === 'file')

    return (
      <div className="flex flex-col gap-4 p-4 flex-1 min-h-0 overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div>
            <div className="text-[13px] font-semibold text-ink">
              <a href={status.repo_url} target="_blank" rel="noopener noreferrer"
                 className="text-blue-600 hover:underline">{status.repo_full_name}</a>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <select value={taHint} onChange={e => setTaHint(e.target.value)}
              className="text-[12px] rounded border border-beige-200 bg-beige-50 px-2 py-1.5 outline-none">
              {FOLDERS.map(f => <option key={f} value={f}>{f}</option>)}
            </select>
            <button onClick={() => fileRef.current?.click()} disabled={uploading}
              className="text-[12px] px-3 py-1.5 rounded-full bg-amber text-white hover:opacity-90 disabled:opacity-50">
              {uploading ? 'Staging…' : '＋ Stage file'}
            </button>
            <input ref={fileRef} type="file" className="hidden"
              onChange={e => { handleStage(e.target.files[0]); e.target.value = '' }} />
          </div>
        </div>

        {/* Staged */}
        {staged.length > 0 && (
          <div className="rounded border border-amber/40 bg-amber/5 p-3 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-[12.5px] font-medium text-ink">
                🟡 {staged.length} staged — not committed yet
              </span>
              <div className="flex gap-2">
                <button onClick={() => api.portfolioClearStaged().then(() => setStaged([]))} disabled={committing}
                  className="text-[11.5px] px-2.5 py-1 rounded-full border border-beige-300 text-ink-muted hover:bg-beige-100 disabled:opacity-50">
                  Discard all
                </button>
                <button onClick={handleCommit} disabled={committing}
                  className="text-[11.5px] px-3 py-1 rounded-full bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-50">
                  {committing ? 'Committing…' : `✓ Commit ${staged.length}`}
                </button>
              </div>
            </div>
            {staged.map(s => (
              <div key={s.id || s.path} className="flex items-center gap-2 py-1">
                <FileIcon name={s.filename || s.path} />
                <span className="flex-1 text-[12.5px] text-ink truncate">{s.path}</span>
                {s.id && (
                  <button onClick={() => handleUnstage(s.id)} disabled={committing}
                    className="text-[11px] text-rose-400 hover:text-rose-600 disabled:opacity-50">Remove</button>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Recent Actions */}
        {runs.length > 0 && (
          <div className="rounded border border-beige-200 p-3 space-y-1.5">
            <div className="text-[12.5px] font-medium text-ink">⚙️ Recent GitHub Actions</div>
            {runs.map(run => (
              <a key={run.id} href={run.url} target="_blank" rel="noopener noreferrer"
                 className="flex items-center gap-2 text-[12px] hover:bg-beige-50 px-1 py-1 rounded">
                <StatusDot conclusion={run.conclusion} status={run.status} />
                <span className="flex-1 text-ink truncate">{run.name}</span>
                <span className="text-[11px] text-ink-muted shrink-0">{run.branch} · {run.event}</span>
              </a>
            ))}
          </div>
        )}

        {/* Breadcrumb */}
        {portPath && (
          <div className="flex items-center gap-1 text-[12px] text-ink-muted">
            <button onClick={() => loadFiles('')} className="hover:text-ink">root</button>
            {portPath.split('/').map((seg, i, arr) => (
              <span key={i} className="flex items-center gap-1">
                <span>/</span>
                <button onClick={() => loadFiles(arr.slice(0, i + 1).join('/'))}
                  className={i === arr.length - 1 ? 'text-ink font-medium' : 'hover:text-ink'}>
                  {seg}
                </button>
              </span>
            ))}
          </div>
        )}

        {err && <div className="text-[12px] text-rose-600">{err}</div>}

        {/* Folder quick-access */}
        {!portPath && (
          <div className="flex flex-wrap gap-2">
            {FOLDERS.map(f => (
              <button key={f} onClick={() => loadFiles(f)}
                className="text-[12px] px-3 py-1.5 rounded-full border border-beige-200 bg-beige-50 hover:bg-beige-100 text-ink-soft">
                📂 {f}
              </button>
            ))}
          </div>
        )}

        {/* File list */}
        <div className="flex flex-col divide-y divide-beige-100">
          {loading && <div className="text-[12px] text-ink-muted py-2">Loading…</div>}
          {!loading && files.length === 0 && (
            <div className="text-[12px] text-ink-muted italic py-2">No files yet. Stage one above.</div>
          )}
          {folders.map(f => (
            <button key={f.path} onClick={() => loadFiles(f.path)}
              className="flex items-center gap-2 py-2 text-left hover:bg-beige-50 px-1 rounded">
              <span>📂</span>
              <span className="text-[13px] text-ink font-medium">{f.name}/</span>
            </button>
          ))}
          {fileItems.map(f => (
            <div key={f.path} className="flex items-center gap-2 py-2 px-1 group">
              <FileIcon name={f.name} />
              <a href={f.url} target="_blank" rel="noopener noreferrer"
                 className="flex-1 text-[13px] text-ink hover:underline truncate">{f.name}</a>
              <span className="text-[11px] text-ink-muted shrink-0">
                {f.size ? `${(f.size / 1024).toFixed(1)} KB` : ''}
              </span>
              <button onClick={() => handleDelete(f.path)}
                className="text-[11px] text-rose-400 hover:text-rose-600 opacity-0 group-hover:opacity-100 shrink-0">
                Delete
              </button>
            </div>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
      {/* ── Header ── */}
      <div className="px-5 py-3 flex items-center gap-3 shrink-0"
           style={{ borderBottom: '0.5px solid rgba(0,0,0,0.08)', background: 'rgba(0,0,0,0.02)' }}>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" className="text-ink shrink-0" aria-hidden>
          <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0 0 24 12c0-6.63-5.37-12-12-12z"/>
        </svg>
        <div className="flex-1 min-w-0">
          <h2 className="text-[14px] font-semibold text-ink leading-tight">GitHub Agent</h2>
          <p className="text-[11px] text-ink-muted truncate">
            Repos · Commits · Branches · PRs · Files · Portfolio · Classroom
          </p>
        </div>
        {tab === 'Chat' && (
          <button onClick={() => setShowSidebar(s => !s)}
            className={`flex items-center gap-1.5 text-[11px] px-2 py-1 rounded border transition-all ${
              showSidebar ? 'bg-beige-200 text-ink border-beige-300' : 'bg-transparent text-ink-muted border-transparent hover:bg-beige-100 hover:text-ink'
            }`}>
            <svg width="13" height="13" viewBox="0 0 13 13" fill="none" aria-hidden>
              <rect x="1" y="1" width="3.5" height="11" rx="1" fill="currentColor" opacity={showSidebar ? '0.8' : '0.4'} />
              <rect x="6" y="1" width="6" height="2.5" rx="0.75" fill="currentColor" opacity="0.4" />
              <rect x="6" y="5.25" width="6" height="2.5" rx="0.75" fill="currentColor" opacity="0.4" />
              <rect x="6" y="9.5" width="6" height="2.5" rx="0.75" fill="currentColor" opacity="0.4" />
            </svg>
            History
          </button>
        )}
      </div>

      {/* ── Tabs ── */}
      <div className="flex items-center gap-0 px-4 shrink-0 overflow-x-auto"
           style={{ borderBottom: '0.5px solid rgba(0,0,0,0.07)' }}>
        {TABS.map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={`text-[12px] px-3 py-2.5 border-b-2 transition-colors whitespace-nowrap ${
              tab === t
                ? 'border-amber text-ink font-medium'
                : 'border-transparent text-ink-muted hover:text-ink'
            }`}>
            {t === 'Portfolio' ? '📁 Portfolio'
             : t === 'Repos' ? '🗂 Repos'
             : t === 'Commits' ? '📝 Commits'
             : t === 'Branches' ? '🌿 Branches'
             : t === 'Pull Requests' ? '🔀 Pull Requests'
             : t === 'Classroom' ? '🎓 Classroom'
             : '💬 Chat'}
          </button>
        ))}
      </div>

      {/* ── Tab content ── */}
      <div className="flex flex-1 min-h-0">
        {tab === 'Chat' && showSidebar && (
          <LumenThreadSidebar
            activeThreadId={lumenThreadId}
            onSelectThread={id => loadThread(id)}
            onNewThread={newThread}
          />
        )}
        <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
          {tab === 'Portfolio' && <PortfolioTab />}
          {tab === 'Chat' && (
            <LumenChat
              messages={lumenMessages}
              onSend={sendLumen}
              typing={lumenTyping}
              onConfirmProposal={confirmProposal}
              onSpeakEndRef={onSpeakEndRef}
              overrideChips={CHAT_CHIPS}
            />
          )}
        </div>
      </div>
    </div>
  )
}
