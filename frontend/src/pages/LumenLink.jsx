import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { getStoredToken } from '../lib/auth.js'
import { api } from '../lib/api.js'

// Shareable Lumen link handler for `/u/:username` (and subdomain links like
// `manohar.lumen.org`, which the app bootstrap rewrites to this route).
//
// Behaviour:
//   - Not signed in → remember the target and send the visitor to /login.
//   - Signed in + the link is the visitor's OWN Lumen → open their interface.
//   - Signed in + someone else's Lumen → open peer-to-peer comms with them.
export default function LumenLink() {
  const { username } = useParams()
  const nav = useNavigate()
  const [err, setErr] = useState('')

  useEffect(() => {
    let cancelled = false

    if (!getStoredToken()) {
      // Survive the login round-trip (Login always returns to "/").
      try { localStorage.setItem('lumen.pendingLink', username) } catch {}
      nav('/login', { replace: true })
      return
    }

    api.lumenLink(username)
      .then(res => {
        if (cancelled) return
        if (res.relationship === 'self') {
          nav('/', { replace: true })
        } else {
          // Peer link → isolated peer-to-peer chat page.
          // Pass the peer's display name via state so PeerChat can show it
          // before the thread loads.
          nav(`/peer-chat/${encodeURIComponent(res.target_id)}`, {
            replace: true,
            state: { peerName: res.profile?.name, username },
          })
        }
      })
      .catch(e => {
        if (cancelled) return
        setErr(e?.message?.includes('404') ? `No Lumen called "${username}"` : 'This Lumen is unavailable')
        setTimeout(() => nav('/', { replace: true }), 1800)
      })

    return () => { cancelled = true }
  }, [username])

  return (
    <div style={{ padding: 24, fontSize: 14, color: '#6b6b6b' }}>
      {err || 'Opening Lumen…'}
    </div>
  )
}
