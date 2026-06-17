import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App.jsx'
import './index.css'

// Apply saved theme before first render to prevent flash
if (localStorage.getItem('lumen.theme') === 'dark') {
  document.documentElement.classList.add('dark')
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
)

// Dev helper: paste a Graph Explorer token to unblock Mail/Files without admin consent.
// Usage in browser console: window.__lumen.seedGraphToken('ey...')
import('./lib/auth.js').then(({ devSeedGraphToken }) => {
  window.__lumen = window.__lumen || {}
  window.__lumen.seedGraphToken = (token) =>
    devSeedGraphToken(token)
      .then(() => console.log('✅ Graph token seeded — mail/files should work now'))
      .catch(e => console.error('❌ Seed failed:', e))
})
