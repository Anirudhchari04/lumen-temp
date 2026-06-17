import { TAS_BY_ID } from '../constants/taConfig.js'

// Guard: detect raw JWT tokens (eyJ... base64 blobs) and redact them.
function isJWT(text) {
  if (typeof text !== 'string') return false
  // JWTs start with eyJ and consist of three base64url parts separated by dots
  return /^eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}$/.test(text.trim()) ||
    // also catch very long strings that are just base64url with no spaces (tokens pasted accidentally)
    (text.trim().length > 200 && /^[A-Za-z0-9+/=_-]+$/.test(text.trim()) && !text.includes(' '))
}

// Inline renderer: **bold**, *italic*, `code`, [text](url) links
function renderInline(text) {
  const parts = []
  // Expanded regex to include markdown links [text](url)
  const re = /(\[([^\]]+)\]\((https?:\/\/[^)]+)\)|\*\*[^*\n]+\*\*|\*[^*\n]+\*|`[^`\n]+`)/g
  let last = 0
  let match
  let key = 0
  while ((match = re.exec(text)) !== null) {
    if (match.index > last) parts.push(<span key={key++}>{text.slice(last, match.index)}</span>)
    const tok = match[0]
    if (tok.startsWith('[')) {
      // Markdown link: [label](url)
      const label = match[2]
      const href  = match[3]
      parts.push(
        <a
          key={key++}
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="text-amber underline underline-offset-2 hover:opacity-80 transition-opacity"
        >
          {label}
        </a>
      )
    } else if (tok.startsWith('**')) {
      parts.push(<strong key={key++} className="font-semibold text-ink">{tok.slice(2, -2)}</strong>)
    } else if (tok.startsWith('`')) {
      parts.push(
        <code key={key++} className="px-1 py-0.5 rounded-md bg-beige-100 text-[0.88em] font-mono text-ink-soft">
          {tok.slice(1, -1)}
        </code>
      )
    } else {
      parts.push(<em key={key++} className="italic">{tok.slice(1, -1)}</em>)
    }
    last = match.index + tok.length
  }
  if (last < text.length) parts.push(<span key={key++}>{text.slice(last)}</span>)
  return parts
}

function renderMarkdown(text) {
  if (!text) return null
  const lines = String(text).split(/\r?\n/)
  const blocks = []
  let listType = null
  let listItems = []

  const flushList = () => {
    if (!listItems.length) return
    const Tag = listType === 'ol' ? 'ol' : 'ul'
    blocks.push(
      <Tag key={'l' + blocks.length}
           className={`${Tag === 'ol' ? 'list-decimal' : 'list-disc'} pl-5 my-1.5 space-y-1`}>
        {listItems.map((it, i) => (
          <li key={i} className="leading-relaxed">{renderInline(it)}</li>
        ))}
      </Tag>
    )
    listType = null
    listItems = []
  }

  for (const rawLine of lines) {
    const line = rawLine.replace(/\t/g, '  ')
    const bullet  = line.match(/^\s*[-•]\s+(.*)$/)
    const numbered = line.match(/^\s*\d+\.\s+(.*)$/)

    if (bullet) {
      if (listType && listType !== 'ul') flushList()
      listType = 'ul'; listItems.push(bullet[1]); continue
    }
    if (numbered) {
      if (listType && listType !== 'ol') flushList()
      listType = 'ol'; listItems.push(numbered[1]); continue
    }
    flushList()

    if (line.trim() === '') {
      blocks.push(<div key={'s' + blocks.length} className="h-2" />)
      continue
    }

    const h = line.match(/^(#{1,3})\s+(.*)$/)
    if (h) {
      const lvl = h[1].length
      const cls = lvl === 1
        ? 'text-[14.5px] font-semibold text-ink mt-1'
        : lvl === 2
          ? 'text-[13.5px] font-semibold text-ink mt-0.5'
          : 'text-[13px] font-medium text-ink-soft mt-0.5'
      blocks.push(
        <div key={'h' + blocks.length} className={cls}>{renderInline(h[2])}</div>
      )
      continue
    }

    blocks.push(
      <div key={'p' + blocks.length} className="leading-relaxed">{renderInline(line)}</div>
    )
  }
  flushList()
  return blocks
}

// A single chat bubble. Supports user, lumen, ta roles.
export default function MessageBubble({ role, content, timestamp, taId, small = false, senderLabel, children }) {
  // Redact raw auth tokens — never show them in the UI
  const safeContent = isJWT(content)
    ? '⚠️ An auth token was detected and redacted. Please do not paste tokens into the chat.'
    : content

  if (role === 'user') {
    return (
      <div className="flex justify-end">
        <div
          className={[
            'max-w-[76%] px-4 py-2.5 leading-relaxed break-words whitespace-pre-wrap',
            'bg-amber text-white',
            small ? 'text-[12px]' : 'text-[13.5px]',
          ].join(' ')}
          style={{ borderRadius: '14px 4px 14px 14px' }}
        >
          {safeContent}
        </div>
      </div>
    )
  }

  // lumen or ta — left-anchored white bubble
  const ta = taId ? TAS_BY_ID[taId] : null
  return (
    <div className="flex items-start gap-2">
      {ta && (
        <span
          aria-hidden
          className="shrink-0 w-6 h-6 rounded-lg flex items-center justify-center mt-0.5"
          style={{ backgroundColor: ta.bgColor, color: ta.color }}
        >
          <ta.icon size={12} />
        </span>
      )}
      <div className="max-w-[82%]">
        {senderLabel && (
          <div className="text-[10.5px] text-amber font-semibold mb-1 px-1 tracking-wide">
            {senderLabel}
          </div>
        )}
        <div
          className={[
            'glass-card text-ink px-4 py-2.5 leading-relaxed break-words',
            small ? 'text-[12px]' : 'text-[13.5px]',
          ].join(' ')}
          style={{ borderRadius: '4px 14px 14px 14px' }}
        >
          {renderMarkdown(safeContent)}
          {children}
        </div>
      </div>
    </div>
  )
}
