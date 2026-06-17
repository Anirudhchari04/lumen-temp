// Token storage and utility functions.
// Microsoft sign-in uses a lightweight OIDC popup (no MSAL dependency).
// Email features for Entra users are blocked by the backend — no Mail.* scopes
// are ever requested, so the corporate compliance policy is not triggered.

const TOKEN_KEY = 'lumen.token'
const USER_KEY = 'lumen.user'

// Entra app settings — sign-in only, no Graph scopes
const ENTRA_TENANT_ID = '72f988bf-86f1-41af-91ab-2d7cd011db47'
const ENTRA_CLIENT_ID = 'baabcd68-1c44-44bb-ba2e-c6bbc77b216d'

export function getStoredUser() {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY) || 'null')
  } catch {
    return null
  }
}

export function getStoredToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export function isAuthed() {
  return !!getStoredToken()
}

export function storeToken(token, user) {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}

export function signOut() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

// ── Entra ID sign-in via OIDC popup (no MSAL, no Graph scopes) ──────────

/**
 * Open a popup to Microsoft's sign-in page, get back an id_token, and POST it
 * to /auth/entra-login. Returns the Lumen JWT + user object.
 *
 * Uses implicit flow with response_type=id_token. Only OIDC scopes
 * (openid profile email) — no Mail.Read / Mail.Send. This means corporate
 * tenant policies on Graph scopes are NEVER triggered.
 */
export async function signIn() {
  const nonce = crypto.randomUUID()
  const state = crypto.randomUUID()
  const redirectUri = window.location.origin + '/auth/entra-callback'

  // Save nonce/state for verification when popup returns
  sessionStorage.setItem('lumen.entra.nonce', nonce)
  sessionStorage.setItem('lumen.entra.state', state)

  const authUrl =
    `https://login.microsoftonline.com/${ENTRA_TENANT_ID}/oauth2/v2.0/authorize` +
    `?client_id=${ENTRA_CLIENT_ID}` +
    `&response_type=id_token` +
    `&redirect_uri=${encodeURIComponent(redirectUri)}` +
    `&scope=${encodeURIComponent('openid profile email')}` +
    `&response_mode=fragment` +
    `&nonce=${nonce}` +
    `&state=${state}` +
    `&prompt=select_account`

  const popup = window.open(authUrl, 'lumen-entra-signin', 'width=500,height=650')
  if (!popup) throw new Error('Popup blocked. Please allow popups and try again.')

  // Wait for the popup to send us the id_token (via postMessage)
  const idToken = await new Promise((resolve, reject) => {
    const timer = setInterval(() => {
      if (popup.closed) { clearInterval(timer); window.removeEventListener('message', onMsg); reject(new Error('Sign-in window was closed')) }
    }, 500)
    function onMsg(event) {
      if (event.origin !== window.location.origin) return
      const d = event.data
      if (!d || d.type !== 'lumen.entra.callback') return
      window.removeEventListener('message', onMsg)
      clearInterval(timer)
      if (d.error) {
        // Don't close the popup on error — let user read the explanation
        if (d.error === 'unsupported_response_type') {
          reject(new Error('Microsoft sign-in not configured: the Entra app needs "ID tokens" enabled under Authentication → Implicit grant. See the popup for the fix.'))
        } else {
          reject(new Error(`Microsoft sign-in failed: ${d.error_description || d.error}`))
        }
        return
      }
      if (d.state !== sessionStorage.getItem('lumen.entra.state')) {
        try { popup.close() } catch {}
        reject(new Error('State mismatch — possible CSRF'))
        return
      }
      try { popup.close() } catch {}
      resolve(d.id_token)
    }
    window.addEventListener('message', onMsg)
  })

  // Exchange the Entra id_token for a Lumen JWT
  const resp = await fetch('/auth/entra-login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ idToken }),
  })
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: 'Login failed' }))
    throw new Error(err.detail || 'Entra login failed')
  }
  const data = await resp.json()
  storeToken(data.token, data.user)
  return data
}

// ── Google OAuth (Drive + Gmail + identity) ──────────────────────────

/**
 * Internal: open the Google OAuth popup and wait for the callback message.
 * If `withLumenToken=true`, the backend treats this as a cross-account Connect
 * (uses the existing Lumen JWT); otherwise it's a sign-in flow (returns a new JWT).
 */
async function _runGoogleOAuth({ withLumenToken, mode }) {
  const headers = { 'Content-Type': 'application/json' }
  if (withLumenToken) {
    const t = getStoredToken()
    if (!t) throw new Error('Sign in to Lumen first.')
    headers['Authorization'] = `Bearer ${t}`
  }
  const qs = mode ? `?mode=${encodeURIComponent(mode)}` : ''
  const urlResp = await fetch(`/auth/google-authorize-url${qs}`, { headers })
  if (!urlResp.ok) {
    const t = await urlResp.text()
    throw new Error(`Google OAuth not configured: ${t}`)
  }
  const { url } = await urlResp.json()

  const popup = window.open(url, 'lumen-google-signin', 'width=560,height=720')
  if (!popup) throw new Error('Popup blocked. Allow popups and try again.')

  return await new Promise((resolve, reject) => {
    const timer = setInterval(() => {
      if (popup.closed) {
        clearInterval(timer)
        window.removeEventListener('message', onMsg)
        reject(new Error('Google connect window was closed'))
      }
    }, 500)
    function onMsg(event) {
      if (event.origin !== window.location.origin) return
      const d = event.data
      if (!d || d.type !== 'lumen.google.callback') return
      clearInterval(timer)
      window.removeEventListener('message', onMsg)
      if (d.error) {
        reject(new Error(d.detail || d.error))
      } else {
        resolve(d)
      }
    }
    window.addEventListener('message', onMsg)
  })
}

/** Sign in with Google — identity only (no Gmail/Drive). Returns Lumen JWT + user. */
export async function googleSignIn() {
  const data = await _runGoogleOAuth({ withLumenToken: false })
  if (data?.token && data?.user) {
    storeToken(data.token, data.user)
  }
  return data
}

/**
 * In-app Connect — grants Gmail/Drive/Calendar access to the signed-in user.
 * mode = 'always' (persist, default) or 'once' (this session only, then re-prompt).
 */
export async function googleConnect(mode = 'always') {
  return await _runGoogleOAuth({ withLumenToken: true, mode })
}

export async function getGoogleStatus() {
  const token = getStoredToken()
  if (!token) return { connected: false }
  const r = await fetch('/lumen/google/status', { headers: { Authorization: `Bearer ${token}` } })
  if (!r.ok) return { connected: false }
  return r.json()
}

export async function googleDisconnect() {
  const token = getStoredToken()
  if (!token) return false
  const r = await fetch('/lumen/google/disconnect', {
    method: 'POST', headers: { Authorization: `Bearer ${token}` },
  })
  return r.ok
}

// ── GitHub OAuth (web Authorization Code flow — no PAT) ───────────────

/**
 * Connect GitHub via a popup. Opens GitHub's authorize page; the user clicks
 * "Authorize" and is redirected back, completing automatically. Resolves with
 * { connected, owner } on success.
 */
export async function githubConnect() {
  const token = getStoredToken()
  if (!token) throw new Error('Sign in to Lumen first.')

  const urlResp = await fetch('/portfolio/oauth/authorize-url', {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!urlResp.ok) {
    const t = await urlResp.text()
    throw new Error(`GitHub not configured: ${t}`)
  }
  const { url } = await urlResp.json()

  const popup = window.open(url, 'lumen-github-signin', 'width=560,height=720')
  if (!popup) throw new Error('Popup blocked. Allow popups and try again.')

  return await new Promise((resolve, reject) => {
    const timer = setInterval(() => {
      if (popup.closed) {
        clearInterval(timer)
        window.removeEventListener('message', onMsg)
        reject(new Error('GitHub connect window was closed'))
      }
    }, 500)
    function onMsg(event) {
      if (event.origin !== window.location.origin) return
      const d = event.data
      if (!d || d.type !== 'lumen.github.callback') return
      clearInterval(timer)
      window.removeEventListener('message', onMsg)
      if (d.error) {
        reject(new Error(d.detail || d.error))
      } else {
        resolve({ connected: true, owner: d.owner || '', portfolio: d.portfolio || null })
      }
    }
    window.addEventListener('message', onMsg)
  })
}

export async function getGithubStatus() {
  const token = getStoredToken()
  if (!token) return { connected: false }
  const r = await fetch('/portfolio/status', { headers: { Authorization: `Bearer ${token}` } })
  if (!r.ok) return { connected: false }
  return r.json()
}

export async function githubDisconnect() {
  const token = getStoredToken()
  if (!token) return false
  const r = await fetch('/portfolio/disconnect', {
    method: 'POST', headers: { Authorization: `Bearer ${token}` },
  })
  return r.ok
}

// ── Notion OAuth ──────────────────────────────────────────────────────

/**
 * Open a Notion OAuth popup. Returns { connected, workspace_name } on success.
 * Backend handles the code-exchange itself.
 */
export async function notionSignIn() {
  const token = getStoredToken()
  if (!token) throw new Error('Sign in to Lumen first.')

  // Get the authorize URL from the backend (which signs the state token)
  const urlResp = await fetch('/auth/notion-authorize-url', {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!urlResp.ok) {
    const t = await urlResp.text()
    throw new Error(`Notion not configured: ${t}`)
  }
  const { url } = await urlResp.json()

  const popup = window.open(url, 'lumen-notion-signin', 'width=560,height=720')
  if (!popup) throw new Error('Popup blocked. Allow popups and try again.')

  return await new Promise((resolve, reject) => {
    const timer = setInterval(() => {
      if (popup.closed) {
        clearInterval(timer)
        window.removeEventListener('message', onMsg)
        reject(new Error('Notion connect window was closed'))
      }
    }, 500)
    function onMsg(event) {
      if (event.origin !== window.location.origin) return
      const d = event.data
      if (!d || d.type !== 'lumen.notion.callback') return
      clearInterval(timer)
      window.removeEventListener('message', onMsg)
      if (d.error) {
        reject(new Error(d.detail || d.error))
      } else {
        resolve({ connected: true, workspace_name: d.workspace_name || '' })
      }
    }
    window.addEventListener('message', onMsg)
  })
}

export async function getNotionStatus() {
  const token = getStoredToken()
  if (!token) return { connected: false }
  const r = await fetch('/lumen/notion/status', { headers: { Authorization: `Bearer ${token}` } })
  if (!r.ok) return { connected: false }
  return r.json()
}

export async function notionDisconnect() {
  const token = getStoredToken()
  if (!token) return false
  const r = await fetch('/lumen/notion/disconnect', {
    method: 'POST', headers: { Authorization: `Bearer ${token}` },
  })
  return r.ok
}

// ── Deprecated Graph token stubs ──────────────────────────────────────
// We intentionally never request Mail.* or Files.* scopes anymore.
// Email features go through IMAP/SMTP instead.

export async function getGraphToken() {
  return null  // No Microsoft Graph token available without MSAL
}

export async function getSmtpToken() {
  return null  // No SMTP OAuth token — use app password via /lumen/email/connect
}

export async function getAIFoundryToken() {
  return null  // Backend uses managed identity instead
}

export async function seedBackendGraphToken() {
  return false  // No-op — Graph token seeding disabled
}

export function startGraphTokenRefresh() {
  // No-op — no MSAL background refresh needed
}

export function stopGraphTokenRefresh() {
  // No-op
}

export function getNeedsReconnect() {
  return false
}

export async function devSeedGraphToken() {
  throw new Error('Graph token seeding disabled')
}
