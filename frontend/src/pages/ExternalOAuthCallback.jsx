// OAuth callback page for the external Outlook PKCE flow.
// This page is opened as a popup; it reads the code/state from the URL
// and posts it back to the parent window, then closes itself.
import { useEffect } from 'react'

export default function ExternalOAuthCallback() {
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const code  = params.get('code')
    const state = params.get('state')
    const error = params.get('error')
    const errorDesc = params.get('error_description')

    if (window.opener) {
      window.opener.postMessage(
        error
          ? { type: 'ext-outlook-callback', error: errorDesc || error }
          : { type: 'ext-outlook-callback', code, state },
        window.location.origin
      )
    }
    window.close()
  }, [])

  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', fontFamily: 'sans-serif', color: '#555' }}>
      Completing sign-in…
    </div>
  )
}
