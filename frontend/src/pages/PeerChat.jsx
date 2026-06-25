import { useEffect, useRef, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../lib/api.js'

// ── Isolated peer-to-peer chat ──────────────────────────────────────────────
//
// Reached via /peer-chat/:peerId (from a shared Lumen link or the Peers page).
// This page is intentionally minimal: NO sidebar, NO your own agents, NO nav
// rail. The ONLY thing that happens here is you ↔ the peer's Lumen.
//
// Routing is enforced on the backend: POST /lumen/peer-chat/{peer_id} routes
// only through the target peer's Lumen — the caller's agents are never invoked.

function Avatar({ name, size = 36 }) {
  const initials = (name || '?')
    .split(/\s+/).filter(Boolean).slice(0, 2)
    .map(s => s[0]?.toUpperCase()).join('')
  const hue = [...(name || '')].reduce((h, c) => (h * 31 + c.charCodeAt(0)) & 0xffff, 7) % 360
  return (
    <div style={{
      width: size, height: size, borderRadius: '50%', flexShrink: 0,
      background: `hsl(${hue},45%,62%)`,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontSize: size * 0.38, fontWeight: 600, color: '#fff', userSelect: 'none',
    }}>
      {initials}
    </div>
  )
}

function Bubble({ msg, myId }) {
  const mine = msg.from_id === myId
  return (
    <div style={{
      display: 'flex', flexDirection: mine ? 'row-reverse' : 'row',
      alignItems: 'flex-end', gap: 8, marginBottom: 12,
    }}>
      {!mine && <Avatar name={msg.from_name} size={28} />}
      <div style={{
        maxWidth: '70%', padding: '9px 13px', borderRadius: mine ? '16px 16px 4px 16px' : '16px 16px 16px 4px',
        background: mine ? '#1a1a1a' : '#f3f0eb',
        color: mine ? '#fff' : '#1a1a1a',
        fontSize: 13.5, lineHeight: 1.55, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
      }}>
        {!mine && (
          <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 3, opacity: 0.55 }}>
            {msg.sender_display || msg.from_name}
          </div>
        )}
        {msg.message}
      </div>
    </div>
  )
}

function TypingDots() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
      <div style={{
        width: 28, height: 28, borderRadius: '50%', background: '#e8e4de',
        flexShrink: 0,
      }} />
      <div style={{
        padding: '9px 14px', borderRadius: '16px 16px 16px 4px',
        background: '#f3f0eb', display: 'flex', gap: 4, alignItems: 'center',
      }}>
        {[0, 1, 2].map(i => (
          <span key={i} style={{
            width: 6, height: 6, borderRadius: '50%', background: '#aaa',
            display: 'inline-block',
            animation: 'lumenDotBounce 1.1s ease-in-out infinite',
            animationDelay: `${i * 0.18}s`,
          }} />
        ))}
      </div>
    </div>
  )
}

export default function PeerChat({ user }) {
  const { peerId } = useParams()
  const nav = useNavigate()
  const bottomRef = useRef(null)
  const inputRef = useRef(null)

  const [peer, setPeer] = useState(null)
  const [messages, setMessages] = useState([])
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  const myId = user?.id || user?.sub

  // Load thread on mount.
  useEffect(() => {
    if (!peerId) return
    api.peerChatThread(peerId)
      .then(res => {
        setPeer(res.peer)
        setMessages(res.thread || [])
        setLoading(false)
      })
      .catch(e => {
        setErr(e?.message?.includes('404') ? 'This Lumen is not found.'
             : e?.message?.includes('403') ? 'This Lumen is private.'
             : 'Could not load the conversation.')
        setLoading(false)
      })
  }, [peerId])

  // Scroll to bottom whenever messages change.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, sending])

  const send = useCallback(async () => {
    const msg = text.trim()
    if (!msg || sending) return
    setText('')
    setSending(true)
    try {
      const res = await api.peerChatSend(peerId, msg)
      setMessages(prev => {
        const next = [...prev, res.sent]
        if (res.reply) next.push(res.reply)
        return next
      })
    } catch (e) {
      setErr('Message failed: ' + (e?.message || 'unknown error'))
    } finally {
      setSending(false)
      inputRef.current?.focus()
    }
  }, [text, sending, peerId])

  const onKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  const peerFirst = (peer?.name || 'Peer').split(' ')[0]

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', height: '100dvh',
      background: '#faf8f5', fontFamily: 'system-ui, sans-serif',
    }}>
      <style>{`
        @keyframes lumenDotBounce {
          0%, 80%, 100% { transform: translateY(0); opacity: 0.4 }
          40% { transform: translateY(-5px); opacity: 1 }
        }
      `}</style>

      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12, padding: '14px 20px',
        borderBottom: '1px solid rgba(0,0,0,0.08)', background: '#fff',
        position: 'sticky', top: 0, zIndex: 10,
      }}>
        <button
          onClick={() => nav(-1)}
          style={{
            background: 'none', border: 'none', cursor: 'pointer', padding: 4,
            color: '#666', fontSize: 18, lineHeight: 1, borderRadius: 6,
          }}
          aria-label="Back"
        >←</button>

        {peer && <Avatar name={peer.name} size={38} />}

        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600, fontSize: 15, color: '#1a1a1a', lineHeight: 1.2 }}>
            {peer?.name || (loading ? '…' : 'Unknown')}
          </div>
          {peer?.bio && (
            <div style={{
              fontSize: 12, color: '#888', marginTop: 2,
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>
              {peer.bio}
            </div>
          )}
        </div>

        {/* Visual cue: this is a constrained channel */}
        <div style={{
          fontSize: 11, color: '#999', background: '#f3f0eb',
          borderRadius: 99, padding: '3px 10px', flexShrink: 0,
        }}>
          peer-to-peer
        </div>
      </div>

      {/* Peer profile strip (expertise / interests) */}
      {peer && (peer.expertise || peer.interests) && (
        <div style={{
          padding: '8px 20px', background: '#f8f5f0',
          borderBottom: '1px solid rgba(0,0,0,0.05)',
          fontSize: 12, color: '#777', display: 'flex', gap: 16, flexWrap: 'wrap',
        }}>
          {peer.expertise && <span>🎓 {peer.expertise}</span>}
          {peer.interests && <span>✦ {peer.interests}</span>}
        </div>
      )}

      {/* Message list */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '16px 20px' }}>
        {loading && (
          <div style={{ textAlign: 'center', color: '#aaa', fontSize: 13, marginTop: 40 }}>
            Loading conversation…
          </div>
        )}

        {!loading && err && (
          <div style={{
            textAlign: 'center', color: '#e05', fontSize: 13, marginTop: 40,
            background: '#fff3f3', borderRadius: 10, padding: 16,
          }}>
            {err}
          </div>
        )}

        {!loading && !err && messages.length === 0 && (
          <div style={{
            textAlign: 'center', color: '#aaa', fontSize: 13, marginTop: 60,
            lineHeight: 1.7,
          }}>
            <div style={{ fontSize: 32, marginBottom: 10 }}>👋</div>
            <div>Say hi to <strong>{peerFirst}</strong>'s Lumen.</div>
            <div style={{ fontSize: 12, marginTop: 6, color: '#bbb' }}>
              You'll be chatting with their Lumen — your own agents aren't involved.
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <Bubble key={m.id || i} msg={m} myId={myId} />
        ))}

        {sending && <TypingDots />}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div style={{
        padding: '12px 16px', borderTop: '1px solid rgba(0,0,0,0.08)',
        background: '#fff', display: 'flex', gap: 10, alignItems: 'flex-end',
      }}>
        <textarea
          ref={inputRef}
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={onKey}
          placeholder={`Message ${peerFirst}'s Lumen…`}
          rows={1}
          disabled={sending || !!err}
          style={{
            flex: 1, resize: 'none', border: '1.5px solid #e5e0d8',
            borderRadius: 12, padding: '10px 14px', fontSize: 14,
            fontFamily: 'inherit', outline: 'none', lineHeight: 1.5,
            background: err ? '#fafafa' : '#fff', color: '#1a1a1a',
            maxHeight: 120, overflowY: 'auto',
            transition: 'border-color .15s',
          }}
          onFocus={e => { e.target.style.borderColor = '#1a1a1a' }}
          onBlur={e => { e.target.style.borderColor = '#e5e0d8' }}
        />
        <button
          onClick={send}
          disabled={!text.trim() || sending || !!err}
          style={{
            width: 40, height: 40, borderRadius: '50%', border: 'none',
            cursor: !text.trim() || sending || err ? 'default' : 'pointer',
            background: !text.trim() || sending || err ? '#e5e0d8' : '#1a1a1a',
            color: '#fff', fontSize: 18, display: 'flex',
            alignItems: 'center', justifyContent: 'center',
            flexShrink: 0, transition: 'background .15s',
          }}
          aria-label="Send"
        >↑</button>
      </div>
    </div>
  )
}
