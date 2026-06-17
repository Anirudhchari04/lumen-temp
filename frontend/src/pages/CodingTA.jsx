import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api.js'

const TYPES = [
  { id: 'auto',     label: 'Auto' },
  { id: 'code',     label: '💻 Code' },
  { id: 'quiz',     label: '❓ Quiz' },
  { id: 'notes',    label: '📝 Notes' },
  { id: 'exercise', label: '🏋️ Exercise' },
]

const TYPE_EMOJI = { code: '💻', quiz: '❓', notes: '📝', exercise: '🏋️', file: '📎' }

const SUGGESTIONS = [
  'Write a binary search in Python',
  'Quiz me on JavaScript closures',
  'Notes on Big-O notation',
  'Exercises on recursion',
]

function ArtifactCard({ item }) {
  const emoji = TYPE_EMOJI[item.type] || '📎'
  const copy = () => { try { navigator.clipboard?.writeText(item.content || '') } catch {} }
  return (
    <div className="rounded-card border border-beige-200 dark:border-gray-700 overflow-hidden bg-white dark:bg-gray-900">
      <div className="flex items-center gap-2 px-3 py-2 bg-beige-50 dark:bg-gray-800 border-b border-beige-200 dark:border-gray-700">
        <span>{emoji}</span>
        <span className="text-[13px] font-medium text-ink dark:text-white flex-1 truncate">{item.title}</span>
        <span className="text-[10.5px] uppercase tracking-wide text-ink-muted px-2 py-0.5 rounded-pill border border-beige-300 dark:border-gray-600 shrink-0">
          {item.type}{item.language ? ` · ${item.language}` : ''}
        </span>
        <button onClick={copy} className="text-[11px] text-ink-muted hover:text-ink px-1.5 shrink-0">Copy</button>
      </div>
      <pre className="text-[12px] leading-relaxed p-3 overflow-x-auto max-h-[380px] whitespace-pre-wrap text-ink dark:text-gray-100">{item.content}</pre>
      <div className="flex items-center gap-2 px-3 py-1.5 text-[11px] border-t border-beige-100 dark:border-gray-700">
        {item.saved ? (
          <>
            <span className="text-emerald-600 shrink-0">✓ Saved to portfolio</span>
            <a href={item.url} target="_blank" rel="noopener noreferrer"
               className="text-blue-600 hover:underline truncate">{item.path}</a>
          </>
        ) : (
          <span className="text-amber-600">
            ⚠ Generated but not saved{item.save_error ? ` — ${item.save_error}` : ' (connect GitHub to auto-save)'}
          </span>
        )}
      </div>
    </div>
  )
}

export default function CodingTA() {
  const [prompt, setPrompt] = useState('')
  const [type, setType]     = useState('auto')
  const [busy, setBusy]     = useState(false)
  const [err, setErr]       = useState('')
  const [items, setItems]   = useState([])
  const [status, setStatus] = useState(null)
  const endRef = useRef(null)

  useEffect(() => { api.portfolioStatus().then(setStatus).catch(() => setStatus(null)) }, [])
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [items, busy])

  const generate = async (text) => {
    const p = (text ?? prompt).trim()
    if (!p || busy) return
    setBusy(true); setErr('')
    setItems(prev => [...prev, { role: 'user', content: p }])
    setPrompt('')
    try {
      const r = await api.codingGenerate(p, type === 'auto' ? '' : type)
      if (!r?.ok) setErr(r?.error || 'Generation failed')
      else setItems(prev => [...prev, { role: 'ta', ...r }])
    } catch (e) {
      setErr(e?.message || 'Generation failed')
    } finally { setBusy(false) }
  }

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); generate() }
  }

  const connected = status?.connected

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Header */}
      <div className="px-4 py-3 border-b border-beige-200 dark:border-gray-700">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-[15px] font-medium text-ink dark:text-white">💻 Coding TA</h2>
            <div className="text-[11.5px] text-ink-muted mt-0.5">
              Ask for code, quizzes, notes or exercises — each is auto-saved to your{' '}
              <a href="/portfolio" className="text-blue-600 hover:underline">portfolio</a>{' '}
              by type &amp; date.
            </div>
          </div>
        </div>
        {connected === false && (
          <div className="mt-2 text-[11.5px] text-amber-700 bg-amber/10 border border-amber/30 rounded-inner px-2.5 py-1.5">
            GitHub isn’t connected, so artifacts will generate but won’t be saved. Connect GitHub from your Profile to enable auto-save.
          </div>
        )}
      </div>

      {/* Conversation */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {items.length === 0 && (
          <div className="text-center text-ink-muted text-[13px] py-8">
            <div className="text-[34px] mb-2">💻</div>
            <div>What should we build or study today?</div>
            <div className="flex flex-wrap gap-2 justify-center mt-4">
              {SUGGESTIONS.map(s => (
                <button key={s} onClick={() => generate(s)}
                  className="text-[12px] px-3 py-1.5 rounded-pill border border-beige-200 bg-beige-50 hover:bg-beige-100 text-ink-soft">
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {items.map((it, i) => (
          it.role === 'user' ? (
            <div key={i} className="flex justify-end">
              <div className="max-w-[80%] text-[13px] px-3 py-2 rounded-card bg-amber text-white">{it.content}</div>
            </div>
          ) : (
            <ArtifactCard key={i} item={it} />
          )
        ))}

        {busy && <div className="text-[12px] text-ink-muted italic">Coding TA is working…</div>}
        {err && <div className="text-[12px] text-rose-600">{err}</div>}
        <div ref={endRef} />
      </div>

      {/* Input */}
      <div className="border-t border-beige-200 dark:border-gray-700 p-3 space-y-2">
        <div className="flex flex-wrap gap-1.5">
          {TYPES.map(t => (
            <button key={t.id} onClick={() => setType(t.id)}
              className={`text-[11.5px] px-2.5 py-1 rounded-pill border transition-colors ${
                type === t.id
                  ? 'bg-amber text-white border-amber'
                  : 'border-beige-200 bg-beige-50 text-ink-soft hover:bg-beige-100'}`}>
              {t.label}
            </button>
          ))}
        </div>
        <div className="flex items-end gap-2">
          <textarea
            value={prompt}
            onChange={e => setPrompt(e.target.value)}
            onKeyDown={onKeyDown}
            rows={1}
            placeholder="e.g. Write a quicksort in Python, or quiz me on hash maps…"
            className="flex-1 resize-none text-[13px] rounded-inner border border-beige-200 bg-white dark:bg-gray-900 px-3 py-2 outline-none focus:border-amber" />
          <button
            onClick={() => generate()}
            disabled={busy || !prompt.trim()}
            className="text-[13px] px-4 py-2 rounded-pill bg-amber text-white hover:opacity-90 disabled:opacity-50 shrink-0">
            {busy ? '…' : 'Create'}
          </button>
        </div>
      </div>
    </div>
  )
}
