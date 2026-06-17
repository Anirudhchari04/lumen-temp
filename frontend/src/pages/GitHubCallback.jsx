import { useEffect, useState } from 'react'

/**
 * GitHub OAuth callback handler (web Authorization Code flow).
 * GitHub redirects here with ?code=... (success) or ?error=... (failure).
 * Exchange the code via the backend, then notify the opener and close.
 */
export default function GitHubCallback() {
  const [view, setView] = useState({ phase: 'loading', error: '', detail: '' })

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const code = params.get('code')
    const state = params.get('state')
    const error = params.get('error')

    if (error) {
      setView({ phase: 'error', error, detail: 'GitHub declined the connection. Close this window and try again.' })
      try {
        if (window.opener) {
          window.opener.postMessage({ type: 'lumen.github.callback', error }, window.location.origin)
        }
      } catch {}
      return
    }

    if (!code || !state) {
      setView({ phase: 'error', error: 'missing_code', detail: 'GitHub did not return a code.' })
      return
    }

    const token = localStorage.getItem('lumen.token')
    if (!token) {
      setView({ phase: 'error', error: 'not_signed_in', detail: 'Sign in to Lumen first, then reconnect GitHub.' })
      return
    }

    fetch('/portfolio/oauth/callback', {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, state }),
    })
      .then(r => r.ok ? r.json() : r.text().then(t => Promise.reject(t)))
      .then(data => {
        if (window.opener && !window.opener.closed) {
          try {
            window.opener.postMessage(
              { type: 'lumen.github.callback', connected: true, owner: data.owner, portfolio: data.portfolio },
              window.location.origin,
            )
          } catch {}
        }
        setView({ phase: 'success', error: '', detail: `Connected as @${data.owner || 'you'}.` })
        setTimeout(() => { try { window.close() } catch {} }, 600)
      })
      .catch(err => {
        const msg = typeof err === 'string' ? err : String(err)
        setView({ phase: 'error', error: 'exchange_failed', detail: msg })
        try {
          if (window.opener) {
            window.opener.postMessage({ type: 'lumen.github.callback', error: 'exchange_failed', detail: msg }, window.location.origin)
          }
        } catch {}
      })
  }, [])

  const styles = {
    wrap: { padding: 32, fontFamily: '-apple-system, "Segoe UI", sans-serif', background: '#0f0f10', color: '#e7e7ea', minHeight: '100vh' },
    h1: { fontSize: 20, margin: 0 },
    p: { color: '#a8a8b0', marginTop: 8, lineHeight: 1.55 },
    code: { background: '#1a1a1c', padding: '2px 6px', borderRadius: 4, fontFamily: 'monospace', fontSize: 12 },
  }

  if (view.phase === 'loading') {
    return <div style={styles.wrap}><h1 style={styles.h1}>Connecting GitHub…</h1><p style={styles.p}>Exchanging code with GitHub.</p></div>
  }
  if (view.phase === 'success') {
    return <div style={styles.wrap}><h1 style={styles.h1}>GitHub connected</h1><p style={styles.p}>{view.detail}</p><p style={styles.p}>This window will close automatically.</p></div>
  }
  return (
    <div style={styles.wrap}>
      <h1 style={styles.h1}>GitHub connection failed</h1>
      <p style={styles.p}><span style={styles.code}>{view.error}</span></p>
      <p style={styles.p}>{view.detail}</p>
    </div>
  )
}
