import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../lib/api.js'

const FOLDERS = ['general', 'coding-ta', 'math-ta', 'cs-ta', 'science-ta', 'english-ta', 'social-ta']

function FileIcon({ name }) {
  const ext = name.split('.').pop()?.toLowerCase()
  if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg'].includes(ext)) return <span>🖼️</span>
  if (['mp4', 'mov', 'avi', 'webm'].includes(ext)) return <span>🎬</span>
  if (['pdf'].includes(ext)) return <span>📄</span>
  return <span>📎</span>
}

export default function Portfolio() {
  const [status, setStatus]       = useState(null)
  const [path, setPath]           = useState('')
  const [files, setFiles]         = useState([])
  const [loading, setLoading]     = useState(false)
  const [uploading, setUploading] = useState(false)
  const [err, setErr]             = useState('')
  const [taHint, setTaHint]       = useState('general')
  const [staged, setStaged]       = useState([])
  const [committing, setCommitting] = useState(false)
  const [runs, setRuns]           = useState([])
  const fileRef                   = useRef(null)

  const loadStatus = useCallback(async () => {
    try {
      const s = await api.portfolioStatus()
      setStatus(s)
    } catch { setStatus(null) }
  }, [])

  const loadFiles = useCallback(async (p = '') => {
    setLoading(true); setErr('')
    try {
      const r = await api.portfolioFiles(p)
      setFiles(r?.files || [])
      setPath(p)
    } catch (e) {
      setErr(e?.message || 'Failed to load files')
    } finally { setLoading(false) }
  }, [])

  const loadStaged = useCallback(async () => {
    try {
      const r = await api.portfolioStaged()
      setStaged(r?.staged || [])
    } catch { /* ignore */ }
  }, [])

  const loadRuns = useCallback(async () => {
    try {
      const r = await api.portfolioActions(5)
      setRuns(r?.runs || [])
    } catch { setRuns([]) }
  }, [])

  useEffect(() => {
    loadStatus()
  }, [loadStatus])

  useEffect(() => {
    if (status?.repo_exists) { loadFiles(''); loadStaged(); loadRuns() }
  }, [status?.repo_exists, loadFiles, loadStaged, loadRuns])

  // Stage (do NOT commit) — the user reviews staged files and commits explicitly.
  const handleStage = async (file) => {
    if (!file) return
    setUploading(true); setErr('')
    try {
      const r = await api.portfolioStage(file, taHint)
      setStaged(r?.all_staged || [])
    } catch (e) {
      setErr(e?.message || 'Staging failed')
    } finally { setUploading(false) }
  }

  const handleCommit = async () => {
    if (staged.length === 0) return
    setCommitting(true); setErr('')
    try {
      const r = await api.portfolioCommit()
      setStaged(r?.failed?.map(f => ({ path: f.path, filename: f.path.split('/').pop() })) || [])
      await loadStaged()
      await loadFiles(path)
      loadRuns()
      if (r?.failed?.length) {
        setErr(`Committed ${r.committed?.length || 0}, failed ${r.failed.length}.`)
      }
    } catch (e) {
      setErr(e?.message || 'Commit failed')
    } finally { setCommitting(false) }
  }

  const handleUnstage = async (id) => {
    try {
      const r = await api.portfolioUnstage(id)
      setStaged(r?.staged || [])
    } catch (e) { setErr(e?.message || 'Failed to remove staged file') }
  }

  const handleClearStaged = async () => {
    try {
      await api.portfolioClearStaged()
      setStaged([])
    } catch (e) { setErr(e?.message || 'Failed to clear staged files') }
  }

  const handleDelete = async (filePath) => {
    if (!confirm(`Delete ${filePath}?`)) return
    try {
      await api.portfolioDelete(filePath)
      await loadFiles(path)
    } catch (e) {
      setErr(e?.message || 'Delete failed')
    }
  }

  if (!status) {
    return (
      <div className="flex-1 flex items-center justify-center text-ink-muted text-[13px]">
        Loading…
      </div>
    )
  }

  if (!status.connected) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 p-8">
        <div className="text-[40px]">📁</div>
        <div className="text-[15px] font-medium text-ink">Portfolio not connected</div>
        <div className="text-[13px] text-ink-muted text-center max-w-xs">
          Open your <strong>Profile</strong> (top-right) and use <strong>Connect GitHub</strong> to connect the portfolio agent.
        </div>
      </div>
    )
  }

  if (!status.repo_exists) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 p-8">
        <div className="text-[40px]">📦</div>
        <div className="text-[15px] font-medium text-ink">Portfolio repo not initialized</div>
        <button
          onClick={() => api.portfolioInit().then(loadStatus)}
          className="text-[13px] px-4 py-2 rounded-pill bg-gray-800 text-white hover:bg-gray-700">
          Create portfolio repo
        </button>
      </div>
    )
  }

  const folders = files.filter(f => f.type === 'dir')
  const fileItems = files.filter(f => f.type === 'file')

  return (
    <div className="flex-1 flex flex-col min-h-0 p-4 gap-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-[15px] font-medium text-ink">📁 Portfolio</h2>
          <div className="text-[11px] text-ink-muted mt-0.5">
            <a href={status.repo_url} target="_blank" rel="noopener noreferrer"
               className="text-blue-600 hover:underline">
              {status.repo_full_name}
            </a>
          </div>
        </div>
        {/* Upload row */}
        <div className="flex items-center gap-2">
          <select
            value={taHint}
            onChange={e => setTaHint(e.target.value)}
            className="text-[12px] rounded-inner border border-beige-200 bg-beige-50 px-2 py-1.5 outline-none">
            {FOLDERS.map(f => (
              <option key={f} value={f}>{f}</option>
            ))}
          </select>
          <button
            onClick={() => fileRef.current?.click()}
            disabled={uploading}
            className="text-[12px] px-3 py-1.5 rounded-pill bg-amber text-white hover:opacity-90 disabled:opacity-50">
            {uploading ? 'Staging…' : '＋ Stage file'}
          </button>
          <input ref={fileRef} type="file" className="hidden"
            onChange={e => { handleStage(e.target.files[0]); e.target.value = '' }} />
        </div>
      </div>

      {/* Staged changes — nothing is committed to GitHub until you click Commit */}
      {staged.length > 0 && (
        <div className="rounded-card border border-amber/40 bg-amber/5 dark:bg-amber/10 p-3 space-y-2">
          <div className="flex items-center justify-between">
            <div className="text-[12.5px] font-medium text-ink dark:text-white">
              🟡 {staged.length} staged change{staged.length > 1 ? 's' : ''} — not committed yet
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={handleClearStaged}
                disabled={committing}
                className="text-[11.5px] px-2.5 py-1 rounded-pill border border-beige-300 text-ink-muted hover:bg-beige-100 disabled:opacity-50">
                Discard all
              </button>
              <button
                onClick={handleCommit}
                disabled={committing}
                className="text-[11.5px] px-3 py-1 rounded-pill bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-50">
                {committing ? 'Committing…' : `✓ Commit ${staged.length} file${staged.length > 1 ? 's' : ''}`}
              </button>
            </div>
          </div>
          <div className="flex flex-col divide-y divide-amber/20">
            {staged.map(s => (
              <div key={s.id || s.path} className="flex items-center gap-2 py-1.5">
                <FileIcon name={s.filename || s.path} />
                <span className="flex-1 text-[12.5px] text-ink dark:text-white truncate">{s.path}</span>
                <span className="text-[11px] text-ink-muted shrink-0">
                  {s.size ? `${(s.size / 1024).toFixed(1)} KB` : ''}
                </span>
                {s.id && (
                  <button
                    onClick={() => handleUnstage(s.id)}
                    disabled={committing}
                    className="text-[11px] text-rose-400 hover:text-rose-600 shrink-0 disabled:opacity-50">
                    Remove
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* GitHub Actions — recent workflow runs */}
      {runs.length > 0 && (
        <div className="rounded-card border border-beige-200 dark:border-gray-700 p-3 space-y-1.5">
          <div className="text-[12.5px] font-medium text-ink dark:text-white">⚙️ Recent GitHub Actions</div>
          {runs.map(run => {
            const icon = run.conclusion === 'success' ? '✅'
              : run.conclusion === 'failure' ? '❌'
              : run.status === 'in_progress' || run.status === 'queued' ? '🟡' : '⚪'
            return (
              <a key={run.id} href={run.url} target="_blank" rel="noopener noreferrer"
                 className="flex items-center gap-2 text-[12px] hover:bg-beige-50 dark:hover:bg-gray-800 px-1 py-1 rounded">
                <span>{icon}</span>
                <span className="flex-1 text-ink dark:text-white truncate">{run.name}</span>
                <span className="text-[11px] text-ink-muted shrink-0">{run.branch} · {run.event}</span>
              </a>
            )
          })}
        </div>
      )}

      {/* Breadcrumb */}
      {path && (
        <div className="flex items-center gap-1 text-[12px] text-ink-muted">
          <button onClick={() => loadFiles('')} className="hover:text-ink">root</button>
          {path.split('/').map((seg, i, arr) => (
            <span key={i} className="flex items-center gap-1">
              <span>/</span>
              <button
                onClick={() => loadFiles(arr.slice(0, i + 1).join('/'))}
                className={i === arr.length - 1 ? 'text-ink font-medium' : 'hover:text-ink'}>
                {seg}
              </button>
            </span>
          ))}
        </div>
      )}

      {err && <div className="text-[12px] text-rose-600">{err}</div>}

      {/* Folder tabs (only at root) */}
      {!path && (
        <div className="flex flex-wrap gap-2">
          {FOLDERS.map(f => (
            <button key={f}
              onClick={() => loadFiles(f)}
              className="text-[12px] px-3 py-1.5 rounded-pill border border-beige-200 bg-beige-50 hover:bg-beige-100 text-ink-soft">
              📂 {f}
            </button>
          ))}
        </div>
      )}

      {/* File list */}
      <div className="flex-1 overflow-y-auto">
        {loading && <div className="text-[12px] text-ink-muted">Loading…</div>}
        {!loading && files.length === 0 && (
          <div className="text-[12px] text-ink-muted italic">No files yet. Upload one above.</div>
        )}
        <div className="flex flex-col divide-y divide-beige-100">
          {folders.map(f => (
            <button key={f.path}
              onClick={() => loadFiles(f.path)}
              className="flex items-center gap-2 py-2 text-left hover:bg-beige-50 px-1 rounded">
              <span>📂</span>
              <span className="text-[13px] text-ink font-medium">{f.name}/</span>
            </button>
          ))}
          {fileItems.map(f => (
            <div key={f.path} className="flex items-center gap-2 py-2 px-1 group">
              <FileIcon name={f.name} />
              <a href={f.url} target="_blank" rel="noopener noreferrer"
                 className="flex-1 text-[13px] text-ink hover:underline truncate">
                {f.name}
              </a>
              <span className="text-[11px] text-ink-muted shrink-0">
                {f.size ? `${(f.size / 1024).toFixed(1)} KB` : ''}
              </span>
              <button
                onClick={() => handleDelete(f.path)}
                className="text-[11px] text-rose-400 hover:text-rose-600 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                Delete
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
