import { useEffect, useState } from 'react'

/**
 * Entra ID OIDC callback handler.
 * Microsoft redirects here with id_token (success) or error (failure) in the URL fragment.
 * Extract it, postMessage to opener, close popup. Show errors visibly if we can't close.
 */
export default function EntraCallback() {
  const [view, setView] = useState({ phase: 'loading', error: '', detail: '' })

  useEffect(() => {
    console.log('[EntraCallback] mounted at', window.location.href)
    const fragment = window.location.hash.slice(1)
    const params = new URLSearchParams(fragment)
    const idToken = params.get('id_token')
    const state = params.get('state')
    const error = params.get('error')
    const errorDescription = params.get('error_description')
    console.log('[EntraCallback] fragment keys:', [...params.keys()])
    console.log('[EntraCallback] error:', error, errorDescription)
    console.log('[EntraCallback] id_token present:', !!idToken)
    console.log('[EntraCallback] window.opener present:', !!window.opener)

    const message = error
      ? { type: 'lumen.entra.callback', error, error_description: errorDescription }
      : { type: 'lumen.entra.callback', id_token: idToken, state }

    // Try to post to opener
    let posted = false
    if (window.opener && !window.opener.closed) {
      try {
        window.opener.postMessage(message, window.location.origin)
        posted = true
      } catch (e) {
        // Cross-origin or permission error — fall through to visible UI
      }
    }

    if (error) {
      // Show the error in the popup itself so the user sees what went wrong
      setView({
        phase: 'error',
        error: decodeURIComponent(error),
        detail: decodeURIComponent(errorDescription || '').replace(/\+/g, ' '),
      })
      return
    }

    if (posted) {
      setView({ phase: 'success', error: '', detail: '' })
      // Give opener time to receive message, then close
      setTimeout(() => { try { window.close() } catch {} }, 300)
    } else {
      setView({
        phase: 'no-opener',
        error: 'Could not reach the Lumen tab',
        detail: 'The popup couldn\'t talk to its opener. Close this window and try again — make sure popup blockers are off.',
      })
    }
  }, [])

  const styles = {
    wrap: {
      padding: 32, fontFamily: '-apple-system, "Segoe UI", sans-serif',
      background: '#0f0f10', color: '#e7e7ea', minHeight: '100vh',
      display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', gap: 16,
    },
    title: { fontSize: 18, fontWeight: 600, margin: 0 },
    sub: { fontSize: 13, color: '#9a9a9f', textAlign: 'center', maxWidth: 480, lineHeight: 1.5 },
    code: {
      fontFamily: 'ui-monospace, "Cascadia Code", monospace',
      fontSize: 11, padding: '8px 12px', background: '#1f1f23',
      border: '1px solid #2a2a2f', borderRadius: 6, color: '#f59e0b',
      maxWidth: 480, wordBreak: 'break-word',
    },
    button: {
      marginTop: 8, padding: '8px 16px', background: '#f59e0b', color: '#000',
      border: 'none', borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: 'pointer',
    },
    hint: {
      marginTop: 12, padding: 12, background: '#1f1f23',
      border: '1px solid #2a2a2f', borderRadius: 6, fontSize: 12,
      color: '#e7e7ea', maxWidth: 480, lineHeight: 1.5,
    },
  }

  if (view.phase === 'success') {
    return (
      <div style={styles.wrap}>
        <div style={{ ...styles.title, color: '#34d399' }}>✓ Signed in</div>
        <div style={styles.sub}>Closing window…</div>
      </div>
    )
  }

  if (view.phase === 'loading') {
    return (
      <div style={styles.wrap}>
        <div style={styles.title}>Signing you in…</div>
      </div>
    )
  }

  // Error or no-opener
  const fixHint = view.error === 'unsupported_response_type' ? (
    <div style={styles.hint}>
      <strong>To fix:</strong>
      <ol style={{ paddingLeft: 18, marginTop: 6, marginBottom: 0 }}>
        <li>Go to <strong>Azure Portal → App registrations</strong></li>
        <li>Find your Lumen app (or contact your admin)</li>
        <li>Click <strong>Authentication → Implicit grant and hybrid flows</strong></li>
        <li>Check <strong>"ID tokens (used for implicit and hybrid flows)"</strong></li>
        <li>Save and try again</li>
      </ol>
    </div>
  ) : null

  return (
    <div style={styles.wrap}>
      <div style={{ ...styles.title, color: '#f87171' }}>✗ Sign-in failed</div>
      <div style={styles.sub}>Microsoft rejected the sign-in request:</div>
      <div style={styles.code}>
        <strong>{view.error}</strong>
        {view.detail && <div style={{ marginTop: 6, color: '#e7e7ea' }}>{view.detail}</div>}
      </div>
      {fixHint}
      <button style={styles.button} onClick={() => window.close()}>Close window</button>
    </div>
  )
}
