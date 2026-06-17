import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { signIn as entraSignIn, googleSignIn } from '../lib/auth.js'

export default function Login() {
  const nav = useNavigate()
  const [googleClientId, setGoogleClientId] = useState(null)

  // Tab state: 'email' | 'google' | 'microsoft'
  const [tab, setTab] = useState('email')

  // Email/password form
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [isRegister, setIsRegister] = useState(false)

  // UI state
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    fetch('/auth/google-client-id')
      .then(r => r.json())
      .then(d => { if (d.clientId) setGoogleClientId(d.clientId) })
      .catch(() => {})
  }, [])

  // Load Google Sign-In script
  useEffect(() => {
    if (!googleClientId) return
    if (document.getElementById('google-signin-script')) return
    const script = document.createElement('script')
    script.id = 'google-signin-script'
    script.src = 'https://accounts.google.com/gsi/client'
    script.async = true
    script.onload = () => {
      window.google?.accounts.id.initialize({ client_id: googleClientId, callback: handleGoogleLogin })
      window.google?.accounts.id.renderButton(
        document.getElementById('google-signin-btn'),
        { theme: 'outline', size: 'large', width: 320, text: 'signin_with' }
      )
    }
    document.head.appendChild(script)
  }, [googleClientId])

  const handleGoogleLogin = async (response) => {
    setErr(''); setLoading(true)
    try {
      const r = await fetch('/auth/google-login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ credential: response.credential }),
      })
      if (!r.ok) throw new Error('Google login failed: ' + r.status)
      const data = await r.json()
      if (data.token) {
        localStorage.setItem('lumen.token', data.token)
        localStorage.setItem('lumen.user', JSON.stringify(data.user))
        nav('/', { replace: true })
      }
    } catch (e) {
      setErr(e?.message || 'Google sign-in failed')
    } finally {
      setLoading(false)
    }
  }

  // Microsoft Entra ID sign-in (OIDC only — no Graph scopes)
  const handleMicrosoftLogin = async () => {
    setErr(''); setLoading(true)
    try {
      await entraSignIn()
      nav('/', { replace: true })
    } catch (e) {
      setErr(e?.message || 'Microsoft sign-in failed')
    } finally {
      setLoading(false)
    }
  }

  // Email/password auth
  const handleEmailAuth = async (e) => {
    e.preventDefault()
    setErr('')
    setLoading(true)

    const endpoint = isRegister ? '/auth/register' : '/auth/login'
    const body = isRegister
      ? { email, password, name }
      : { email, password }

    try {
      const r = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) {
        const err_data = await r.json()
        throw new Error(err_data.detail || `${endpoint} failed: ${r.status}`)
      }
      const data = await r.json()
      if (data.token) {
        localStorage.setItem('lumen.token', data.token)
        localStorage.setItem('lumen.user', JSON.stringify(data.user))
        nav('/', { replace: true })
      }
    } catch (e) {
      setErr(e?.message || 'Authentication failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-beige-50 dark:bg-gray-950 flex items-center justify-center p-6">
      <div className="w-full max-w-[360px] flex flex-col items-center gap-4">
        <div className="text-center mb-2">
          <div className="text-[24px] mb-1">✦</div>
          <h1 className="text-[18px] font-medium text-ink dark:text-white">Welcome to Lumen</h1>
          <p className="text-[12px] text-ink-muted dark:text-gray-400 mt-1">Your personal learning companion</p>
        </div>

        {/* Tab buttons */}
        <div className="flex gap-2 w-full mb-2">
          <button
            onClick={() => { setTab('email'); setErr('') }}
            className={`flex-1 py-2 text-[12px] font-medium rounded-lg transition ${
              tab === 'email'
                ? 'bg-ink text-white'
                : 'bg-beige-100 dark:bg-gray-800 text-ink dark:text-white hover:bg-beige-150'
            }`}
          >
            Email
          </button>
          <button
            onClick={() => { setTab('google'); setErr('') }}
            disabled={!googleClientId}
            className={`flex-1 py-2 text-[12px] font-medium rounded-lg transition ${
              tab === 'google'
                ? 'bg-ink text-white'
                : 'bg-beige-100 dark:bg-gray-800 text-ink dark:text-white hover:bg-beige-150 disabled:opacity-50'
            }`}
          >
            Google
          </button>
          <button
            onClick={() => { setTab('microsoft'); setErr('') }}
            className={`flex-1 py-2 text-[12px] font-medium rounded-lg transition ${
              tab === 'microsoft'
                ? 'bg-ink text-white'
                : 'bg-beige-100 dark:bg-gray-800 text-ink dark:text-white hover:bg-beige-150'
            }`}
          >
            Microsoft
          </button>
        </div>

        {/* Email/Password Tab */}
        {tab === 'email' && (
          <form onSubmit={handleEmailAuth} className="w-full flex flex-col gap-3">
            {isRegister && (
              <input
                type="text"
                placeholder="Full name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full px-3 py-2 text-[13px] border border-beige-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-900 text-ink dark:text-white placeholder-gray-400 focus:outline-none focus:border-ink"
              />
            )}
            <input
              type="email"
              placeholder="Email address"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="w-full px-3 py-2 text-[13px] border border-beige-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-900 text-ink dark:text-white placeholder-gray-400 focus:outline-none focus:border-ink"
            />
            <input
              type="password"
              placeholder="Password (min 8 chars)"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              className="w-full px-3 py-2 text-[13px] border border-beige-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-900 text-ink dark:text-white placeholder-gray-400 focus:outline-none focus:border-ink"
            />
            <button
              type="submit"
              disabled={loading}
              className="w-full py-2.5 bg-ink text-white text-[14px] font-medium rounded-lg hover:opacity-90 disabled:opacity-60 transition"
            >
              {loading ? 'Loading…' : isRegister ? 'Create Account' : 'Sign In'}
            </button>
            <button
              type="button"
              onClick={() => { setIsRegister(!isRegister); setErr('') }}
              className="w-full py-1 text-[12px] text-ink-muted dark:text-gray-400 hover:text-ink dark:hover:text-white transition"
            >
              {isRegister ? 'Already have an account? Sign in' : "Don't have an account? Create one"}
            </button>
          </form>
        )}

        {/* Google Tab */}
        {tab === 'google' && (
          <div className="w-full flex flex-col items-center gap-3">
            <p className="text-[12px] text-ink-muted dark:text-gray-400 text-center">
              Sign in with your Google account
            </p>
            <button
              type="button"
              onClick={async () => {
                setErr(''); setLoading(true)
                try {
                  await googleSignIn()
                  nav('/', { replace: true })
                } catch (e) {
                  setErr(e?.message || 'Google sign-in failed')
                } finally {
                  setLoading(false)
                }
              }}
              disabled={loading}
              className="w-full py-2.5 bg-white border border-beige-300 text-ink text-[14px] font-medium rounded-lg hover:bg-beige-50 disabled:opacity-60 transition flex items-center justify-center gap-2"
            >
              <svg width="16" height="16" viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg">
                <path fill="#4285F4" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
                <path fill="#34A853" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
                <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>
                <path fill="#EA4335" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
              </svg>
              {loading ? 'Opening sign-in…' : 'Sign in with Google'}
            </button>
            <div className="text-2xs text-ink-muted dark:text-gray-400 text-center px-3 leading-relaxed">
              Signs you in with your Google identity only. Gmail and Drive access is
              requested later, inside Lumen, the first time you use them — your choice.
            </div>
          </div>
        )}

        {/* Microsoft Tab */}
        {tab === 'microsoft' && (
          <div className="w-full flex flex-col items-center gap-3">
            <p className="text-[12px] text-ink-muted dark:text-gray-400 text-center">
              Sign in with your Microsoft work or school account
            </p>
            <button
              type="button"
              onClick={handleMicrosoftLogin}
              disabled={loading}
              className="w-full py-2.5 bg-[#2f2f2f] text-white text-[14px] font-medium rounded-lg hover:opacity-90 disabled:opacity-60 transition flex items-center justify-center gap-2"
            >
              <svg width="14" height="14" viewBox="0 0 21 21" xmlns="http://www.w3.org/2000/svg">
                <rect x="1" y="1" width="9" height="9" fill="#f25022"/>
                <rect x="11" y="1" width="9" height="9" fill="#7fba00"/>
                <rect x="1" y="11" width="9" height="9" fill="#00a4ef"/>
                <rect x="11" y="11" width="9" height="9" fill="#ffb900"/>
              </svg>
              {loading ? 'Opening sign-in…' : 'Sign in with Microsoft'}
            </button>
            <p className="text-[11px] text-ink-muted dark:text-gray-500 text-center mt-1 px-2 leading-snug">
              Lumen will use your Microsoft session to access mail via the M365
              Agents platform. If your tenant blocks Mail.Send/Mail.Read scopes,
              you'll get an admin-required error — in that case, sign in with
              a personal account instead.
            </p>
          </div>
        )}

        {/* Error message */}
        {err && (
          <div className="w-full text-[12px] text-[#b33a3a] bg-[#fff2f1] border border-[#f3c9c5] rounded-lg p-2.5 text-center">
            {err}
          </div>
        )}
      </div>
    </div>
  )
}
