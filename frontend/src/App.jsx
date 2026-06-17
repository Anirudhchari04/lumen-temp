import { useEffect, useState } from 'react'
import { Routes, Route, Navigate, useLocation, useNavigate } from 'react-router-dom'
import Shell from './components/Shell.jsx'
import IconRail from './components/IconRail.jsx'
import BottomNav from './components/BottomNav.jsx'
import Dashboard from './pages/Dashboard.jsx'
import GitHubAgentPage from './pages/GitHubAgentPage.jsx'
import Peers from './pages/Peers.jsx'
import Privacy from './pages/Privacy.jsx'
import Usage from './pages/Usage.jsx'
import CodingTA from './pages/CodingTA.jsx'
import TAPanel from './pages/TAPanel.jsx'
import Login from './pages/Login.jsx'
import ExternalOAuthCallback from './pages/ExternalOAuthCallback.jsx'
import OutlookSignInHelper from './pages/OutlookSignInHelper.jsx'
import EntraCallback from './pages/EntraCallback.jsx'
import NotionCallback from './pages/NotionCallback.jsx'
import GoogleCallback from './pages/GoogleCallback.jsx'
import GitHubCallback from './pages/GitHubCallback.jsx'
import useLumenSession from './hooks/useLumenSession.js'
import { getStoredToken, getStoredUser, signOut, startGraphTokenRefresh } from './lib/auth.js'
import { api } from './lib/api.js'

function useAuthedUser() {
  const [user, setUser] = useState(getStoredUser())
  const [ready, setReady] = useState(false)
  useEffect(() => {
    if (!getStoredToken()) { setReady(true); return }
    startGraphTokenRefresh()
    api.profile()
      .then(p => {
        const u = { id: p.id, name: p.name, email: p.email }
        localStorage.setItem('lumen.user', JSON.stringify(u))
        setUser(u)
      })
      .catch(() => {})
      .finally(() => setReady(true))
  }, [])
  return { user, ready }
}

function Protected({ children }) {
  const loc = useLocation()
  if (!getStoredToken()) return <Navigate to="/login" replace state={{ from: loc.pathname }} />
  return children
}

function useTheme() {
  const [isDark, setIsDark] = useState(() => localStorage.getItem('lumen.theme') === 'dark')

  useEffect(() => {
    if (isDark) {
      document.documentElement.classList.add('dark')
    } else {
      document.documentElement.classList.remove('dark')
    }
    localStorage.setItem('lumen.theme', isDark ? 'dark' : 'light')
  }, [isDark])

  const toggle = () => setIsDark(d => !d)
  return { isDark, toggle }
}

function AppShell() {
  const nav = useNavigate()
  const loc = useLocation()
  const isV2 = loc.pathname === '/v2' || loc.pathname.startsWith('/v2/')
  const session = useLumenSession({
    onNavigate: (url) => nav(url),
    onExternalLaunch: (url) => window.open(url, '_blank'),
    v2: isV2,
  })
  const { user } = useAuthedUser()
  const { isDark, toggle } = useTheme()

  // Apply the v2 violet/teal theme only while on a /v2 route (scoped — v1 untouched).
  useEffect(() => {
    document.documentElement.classList.toggle('v2', isV2)
    return () => document.documentElement.classList.remove('v2')
  }, [isV2])

  const rail = (
    <IconRail
      user={user}
      onLogout={async () => { await signOut(); nav('/login', { replace: true }) }}
    />
  )
  const bottomNav = <BottomNav />

  return (
    <Shell rail={rail} bottomNav={bottomNav}>
      <Routes>
        <Route path="/"           element={<Dashboard session={session} user={user} isDark={isDark} onToggleTheme={toggle} />} />
        <Route path="/dashboard"  element={<Dashboard session={session} user={user} isDark={isDark} onToggleTheme={toggle} />} />
        {/* Lumen v2 — same Dashboard + login, Magentic-One backend, v2 theme. */}
        <Route path="/v2"         element={<Dashboard session={session} user={user} isDark={isDark} onToggleTheme={toggle} />} />
        <Route path="/peers"      element={<Peers user={user} />} />
        <Route path="/privacy"    element={<Privacy user={user} />} />
        <Route path="/portfolio"  element={<Navigate to="/github" replace />} />
        <Route path="/github"     element={<GitHubAgentPage session={session} />} />
        <Route path="/usage"      element={<Usage user={user} />} />
        <Route path="/coding-ta"  element={<CodingTA />} />
        <Route path="/ta"         element={<TAPanel />} />
        <Route path="*"           element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  )
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      {/* OAuth callback — must be accessible without auth so the popup can render */}
      <Route path="/auth/external-outlook/callback" element={<ExternalOAuthCallback />} />
      {/* Entra ID OIDC popup callback */}
      <Route path="/auth/entra-callback" element={<EntraCallback />} />
      <Route path="/auth/notion-callback" element={<NotionCallback />} />
      <Route path="/auth/google-callback" element={<GoogleCallback />} />
      <Route path="/auth/github-callback" element={<GitHubCallback />} />
      {/* Guided Outlook sign-in popup — opens Microsoft login + token paste flow */}
      <Route path="/outlook-signin" element={<OutlookSignInHelper />} />
      <Route path="/*" element={<Protected><AppShell /></Protected>} />
    </Routes>
  )
}
