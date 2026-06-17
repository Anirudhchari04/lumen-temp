import { useLocation, useNavigate } from 'react-router-dom'
import { IconHome, IconGraduation, IconPeople, IconFolder, IconShield, IconLogout } from './icons.jsx'

// Inline code glyph (</>) — no matching icon in icons.jsx.
const IconCode = ({ size = 16 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
       stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="16 18 22 12 16 6" />
    <polyline points="8 6 2 12 8 18" />
  </svg>
)

// Inline bar-chart glyph — token usage / cost dashboard.
const IconChart = ({ size = 16 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
       stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="18" y1="20" x2="18" y2="10" />
    <line x1="12" y1="20" x2="12" y2="4" />
    <line x1="6" y1="20" x2="6" y2="14" />
  </svg>
)

// Inline sparkle glyph — Lumen v2 (Magentic-One) console.
const IconSparkle = ({ size = 16 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
       stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 3l1.8 4.9L18.7 9l-4.9 1.8L12 15.7 10.2 10.8 5.3 9l4.9-1.1L12 3z" />
  </svg>
)

// Left nav rail — navigation only. Profile + notifications live in TopStrip.
export default function IconRail({ onLogout }) {
  const loc = useLocation()
  const nav = useNavigate()

  const items = [
    { label: 'Home',      Icon: IconHome,       path: '/' },
    { label: 'Courses',   Icon: IconGraduation, path: '/ta' },
    { label: 'Coding TA', Icon: IconCode,       path: '/coding-ta' },
    { label: 'Peers',     Icon: IconPeople,     path: '/peers' },
    { label: 'Portfolio', Icon: IconFolder,     path: '/portfolio' },
    { label: 'Usage',     Icon: IconChart,      path: '/usage' },
    { label: 'Privacy',   Icon: IconShield,     path: '/privacy' },
    { label: 'Lumen v2',  Icon: IconSparkle,    path: '/v2' },
  ]

  const isActive = (p) =>
    (p === '/' && (loc.pathname === '/' || loc.pathname === '/dashboard')) ||
    (p !== '/' && loc.pathname.startsWith(p))

  return (
    <aside
      className="glass-rail w-[52px] shrink-0 flex flex-col items-center py-4 gap-1"
      style={{ borderRight: '0.5px solid var(--border-rail)' }}
    >
      {/* Lumen wordmark dot */}
      <div
        className="w-7 h-7 rounded-full flex items-center justify-center mb-3 shrink-0"
        style={{ background: '#c8762a', boxShadow: '0 2px 8px rgba(200,118,42,0.35)' }}
        title="Lumen"
      >
        <svg width="12" height="12" viewBox="0 0 14 14" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round">
          <path d="M7 2v5l3 2" />
          <circle cx="7" cy="7" r="5.5" />
        </svg>
      </div>

      <div style={{ width: 20, height: 0.5, background: 'rgba(0,0,0,0.12)', margin: '4px 0 8px' }} />

      {items.map(({ label, Icon, path }) => {
        const active = isActive(path)
        return (
          <div key={label} className="relative w-full flex items-center justify-center">
            {active && (
              <span
                className="absolute left-0 top-1/2 -translate-y-1/2 rounded-r-full bg-amber"
                style={{ width: 3, height: 22 }}
              />
            )}
            <button
              onClick={() => nav(path)}
              aria-label={label}
              title={label}
              className="w-9 h-9 rounded-xl flex items-center justify-center transition-all duration-150"
              style={{
                background: active ? 'rgba(200,118,42,0.12)' : 'transparent',
                color: active ? '#c8762a' : 'var(--text-muted)',
              }}
              onMouseEnter={e => { if (!active) e.currentTarget.style.background = 'rgba(0,0,0,0.06)' }}
              onMouseLeave={e => { if (!active) e.currentTarget.style.background = 'transparent' }}
            >
              <Icon size={16} />
            </button>
          </div>
        )
      })}

      <div className="flex-1" />

      {onLogout && (
        <button
          onClick={onLogout}
          aria-label="Sign out"
          title="Sign out"
          className="w-9 h-9 rounded-xl flex items-center justify-center transition-all duration-150"
          style={{ color: 'var(--text-muted)' }}
          onMouseEnter={e => { e.currentTarget.style.background = 'rgba(0,0,0,0.06)'; e.currentTarget.style.color = 'var(--text-soft)' }}
          onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-muted)' }}
        >
          <IconLogout size={15} />
        </button>
      )}
    </aside>
  )
}
