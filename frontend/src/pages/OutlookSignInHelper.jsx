// Outlook device code sign-in popup.
// 1. Calls /auth/outlook-device-code/start → gets user_code
// 2. Shows code + "Open microsoft.com/devicelogin" button
// 3. Polls /auth/outlook-device-code/poll until complete
// 4. postMessages result to parent window and closes
import { useEffect, useState, useRef } from 'react'

const POLL_MS = 4000

export default function OutlookSignInHelper() {
  const [phase, setPhase] = useState('loading')   // loading | code | done | error | noclient
  const [userCode, setUserCode] = useState('')
  const [verifyUri, setVerifyUri] = useState('https://microsoft.com/devicelogin')
  const [sessionId, setSessionId] = useState(null)
  const [copied, setCopied] = useState(false)
  const [errMsg, setErrMsg] = useState('')
  const pollRef = useRef(null)

  useEffect(() => {
    startDeviceCode()
    return () => clearInterval(pollRef.current)
  }, [])

  const startDeviceCode = async () => {
    setPhase('loading')
    try {
      // Check if device code is available
      const cfg = await fetch('/auth/outlook-device-code/config').then(r => r.json())
      if (!cfg.available) { setPhase('noclient'); return }

      const r = await fetch('/auth/outlook-device-code/start', { method: 'POST' })
      if (!r.ok) throw new Error(await r.text())
      const data = await r.json()
      setUserCode(data.user_code)
      setVerifyUri(data.verification_uri || 'https://microsoft.com/devicelogin')
      setSessionId(data.session_id)
      setPhase('code')

      // Start polling
      pollRef.current = setInterval(() => pollStatus(data.session_id), POLL_MS)
    } catch (e) {
      setErrMsg(e.message || 'Could not start sign-in')
      setPhase('error')
    }
  }

  const pollStatus = async (sid) => {
    try {
      const r = await fetch(`/auth/outlook-device-code/poll/${sid}`)
      const data = await r.json()
      if (data.status === 'complete') {
        clearInterval(pollRef.current)
        setPhase('done')
        // Send result to parent Login page
        if (window.opener) {
          window.opener.postMessage(
            { type: 'outlook-login-success', token: data.token, user: data.user },
            window.location.origin
          )
        }
        setTimeout(() => window.close(), 1200)
      } else if (data.status === 'expired') {
        clearInterval(pollRef.current)
        setErrMsg('Code expired. Please try again.')
        setPhase('error')
      } else if (data.status === 'error') {
        clearInterval(pollRef.current)
        setErrMsg(data.error || 'Sign-in failed')
        setPhase('error')
      }
      // pending → keep polling
    } catch { /* network error — keep trying */ }
  }

  const copyCode = () => {
    navigator.clipboard.writeText(userCode).catch(() => {})
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const openDeviceLogin = () => {
    window.open(verifyUri, '_blank', 'width=500,height=650')
  }

  const s = styles

  return (
    <div style={s.page}>
      <div style={s.card}>
        {/* Header */}
        <div style={s.header}>
          <OutlookLogo />
          <h2 style={s.title}>Sign in with Outlook</h2>
          <p style={s.sub}>College or personal Microsoft account</p>
        </div>

        {phase === 'loading' && (
          <div style={s.center}>
            <Spinner />
            <p style={s.hint}>Starting sign-in…</p>
          </div>
        )}

        {phase === 'code' && (
          <>
            <p style={s.instruction}>
              Enter this code at <b>microsoft.com/devicelogin</b> to sign in:
            </p>

            {/* The code — big and easy to read */}
            <div style={s.codeBox} onClick={copyCode} title="Click to copy">
              <span style={s.code}>{userCode}</span>
              <span style={s.copyHint}>{copied ? '✓ Copied' : 'click to copy'}</span>
            </div>

            <button onClick={openDeviceLogin} style={s.primaryBtn}>
              Open microsoft.com/devicelogin ↗
            </button>

            <div style={s.pollRow}>
              <Spinner small />
              <span style={s.pollText}>Waiting for you to sign in…</span>
            </div>

            <p style={s.steps}>
              1. Click the button above<br/>
              2. Paste the code → sign in with your Outlook account<br/>
              3. Microsoft will ask for permission — click <b>Allow</b><br/>
              4. This window closes automatically ✓
            </p>
          </>
        )}

        {phase === 'done' && (
          <div style={s.center}>
            <div style={{ fontSize: 36 }}>✓</div>
            <p style={{ ...s.hint, color: '#107c41', fontWeight: 600 }}>Signed in! Closing…</p>
          </div>
        )}

        {phase === 'noclient' && (
          <div style={s.setupBox}>
            <p style={{ ...s.instruction, color: '#c0392b', marginBottom: 12 }}>
              <b>One-time setup needed</b>
            </p>
            <p style={{ fontSize: 13, color: '#555', lineHeight: 1.6 }}>
              To enable direct Outlook sign-in, register a free app:
            </p>
            <ol style={{ fontSize: 12, color: '#444', lineHeight: 2, paddingLeft: 18 }}>
              <li>Go to <a href="https://portal.azure.com" target="_blank" rel="noopener" style={s.link}>portal.azure.com</a> (any personal MSA)</li>
              <li>New App Registration → <b>"Accounts in any org + personal"</b></li>
              <li>Under <b>Authentication</b> → enable <b>Allow public client flows</b></li>
              <li>Copy the <b>Application (client) ID</b></li>
              <li>Set env var: <code style={s.code2}>EXTERNAL_OUTLOOK_CLIENT_ID=&lt;id&gt;</code></li>
            </ol>
          </div>
        )}

        {phase === 'error' && (
          <div style={s.center}>
            <p style={{ ...s.hint, color: '#c0392b' }}>{errMsg}</p>
            <button onClick={startDeviceCode} style={s.secondaryBtn}>Try again</button>
          </div>
        )}
      </div>
    </div>
  )
}

function OutlookLogo() {
  return (
    <svg width="40" height="40" viewBox="0 0 48 48" style={{ marginBottom: 8 }}>
      <rect width="48" height="48" rx="8" fill="#0078d4"/>
      <rect x="6" y="10" width="22" height="28" rx="3" fill="white" fillOpacity="0.95"/>
      <ellipse cx="17" cy="24" rx="6" ry="7" fill="#0078d4"/>
      <rect x="28" y="14" width="14" height="20" rx="2" fill="white" fillOpacity="0.4"/>
      <path d="M28 20h14M28 24h14M28 28h10" stroke="white" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  )
}

function Spinner({ small }) {
  const size = small ? 14 : 24
  return (
    <div style={{
      width: size, height: size, borderRadius: '50%',
      border: `${small ? 2 : 3}px solid #e0e0e0`,
      borderTopColor: '#0078d4',
      animation: 'spin 0.8s linear infinite',
      display: 'inline-block',
    }} />
  )
}

const styles = {
  page: {
    minHeight: '100vh', background: '#f3f2f1',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontFamily: '"Segoe UI", system-ui, sans-serif', padding: 16,
  },
  card: {
    width: '100%', maxWidth: 380, background: 'white',
    borderRadius: 12, padding: '28px 24px', boxShadow: '0 4px 24px rgba(0,0,0,0.10)',
    display: 'flex', flexDirection: 'column', gap: 16,
  },
  header: { textAlign: 'center', display: 'flex', flexDirection: 'column', alignItems: 'center' },
  title: { margin: 0, fontSize: 18, fontWeight: 600, color: '#1a1a1a' },
  sub: { margin: '4px 0 0', fontSize: 13, color: '#888' },
  center: { display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10, padding: '8px 0' },
  instruction: { fontSize: 14, color: '#333', textAlign: 'center', margin: 0, lineHeight: 1.5 },
  codeBox: {
    background: '#f0f6ff', border: '2px dashed #0078d4', borderRadius: 10,
    padding: '16px', textAlign: 'center', cursor: 'pointer',
    display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'center',
    userSelect: 'all',
  },
  code: {
    fontSize: 28, fontWeight: 700, letterSpacing: 6, color: '#0078d4',
    fontFamily: 'monospace',
  },
  code2: {
    background: '#f0f6ff', padding: '2px 6px', borderRadius: 4,
    fontSize: 11, fontFamily: 'monospace', color: '#0078d4',
  },
  copyHint: { fontSize: 11, color: '#888' },
  primaryBtn: {
    width: '100%', padding: '12px', borderRadius: 8, border: 'none',
    background: '#0078d4', color: 'white', fontSize: 14, fontWeight: 600,
    cursor: 'pointer',
  },
  secondaryBtn: {
    padding: '10px 24px', borderRadius: 8, border: '1px solid #ccc',
    background: 'white', color: '#333', fontSize: 13, cursor: 'pointer',
  },
  pollRow: { display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'center' },
  pollText: { fontSize: 12, color: '#888' },
  hint: { fontSize: 14, color: '#555', margin: 0 },
  steps: {
    fontSize: 12, color: '#777', lineHeight: 1.8, margin: 0,
    background: '#fafafa', borderRadius: 8, padding: '10px 12px',
  },
  setupBox: { background: '#fef9f0', borderRadius: 8, padding: 16 },
  link: { color: '#0078d4' },
}
