import { useEffect, useState } from 'react'

/**
 * Google OAuth callback handler.
 * Two paths:
 *   - Sign-in: caller had no Lumen JWT → backend returns a fresh `token` + `user` → we postMessage them out.
 *   - Cross-account Connect: caller had a Lumen JWT → backend just stores the Google tokens.
 */
export default function GoogleCallback() {
  const [view, setView] = useState({ phase: 'loading', error: '', detail: '' })

  useEffect(() => {
    console.log('[GoogleCallback] mounted at', window.location.href)
    const params = new URLSearchParams(window.location.search)
    const code = params.get('code')
    const state = params.get('state')
    const error = params.get('error')
    console.log('[GoogleCallback] code=', code?.slice(0, 10), 'state=', state?.slice(0, 10), 'error=', error)

    if (error) {
      setView({ phase: 'error', error, detail: 'Google declined the connection. Close this window and try again.' })
      try {
        if (window.opener) {
          window.opener.postMessage({ type: 'lumen.google.callback', error }, window.location.origin)
        }
      } catch {}
      return
    }

    if (!code || !state) {
      setView({ phase: 'error', error: 'missing_code', detail: 'Google did not return a code.' })
      return
    }

    // Forward the existing Lumen JWT if we have one (cross-account Connect path).
    const lumenToken = localStorage.getItem('lumen.token') || ''
    const headers = { 'Content-Type': 'application/json' }
    if (lumenToken) headers['Authorization'] = `Bearer ${lumenToken}`

    fetch('/auth/google-callback', {
      method: 'POST',
      headers,
      body: JSON.stringify({ code, state }),
    })
      .then(r => r.ok ? r.json() : r.text().then(t => Promise.reject(t)))
      .then(data => {
        if (window.opener && !window.opener.closed) {
          try {
            window.opener.postMessage(
              {
                type: 'lumen.google.callback',
                connected: true,
                email: data.email,
                name: data.name,
                scopes: data.scopes,
                token: data.token || null,
                user: data.user || null,
              },
              window.location.origin,
            )
          } catch {}
        }
        setView({ phase: 'success', error: '', detail: `Connected ${data.email || ''}.` })
        setTimeout(() => { try { window.close() } catch {} }, 600)
      })
      .catch(err => {
        const msg = typeof err === 'string' ? err : String(err)
        setView({ phase: 'error', error: 'exchange_failed', detail: msg })
        try {
          if (window.opener) {
            window.opener.postMessage({ type: 'lumen.google.callback', error: 'exchange_failed', detail: msg }, window.location.origin)
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
    return <div style={styles.wrap}><h1 style={styles.h1}>Connecting Google…</h1><p style={styles.p}>Exchanging code with Google.</p></div>
  }
  if (view.phase === 'success') {
    return <div style={styles.wrap}><h1 style={styles.h1}>Google connected</h1><p style={styles.p}>{view.detail}</p><p style={styles.p}>This window will close automatically.</p></div>
  }
  return (
    <div style={styles.wrap}>
      <h1 style={styles.h1}>Google connection failed</h1>
      <p style={styles.p}><span style={styles.code}>{view.error}</span></p>
      <p style={styles.p}>{view.detail}</p>
    </div>
  )
}
