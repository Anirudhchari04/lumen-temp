import { useLocation, useNavigate } from 'react-router-dom'
import { IconHome, IconPeople, IconShield, IconGraduation, IconFolder } from './icons.jsx'

export default function BottomNav() {
  const nav = useNavigate()
  const loc = useLocation()

  const items = [
    { label: 'Home',      Icon: IconHome,       path: '/' },
    { label: 'Courses',   Icon: IconGraduation, path: '/ta' },
    { label: 'Peers',     Icon: IconPeople,     path: '/peers' },
    { label: 'Portfolio', Icon: IconFolder,     path: '/portfolio' },
    { label: 'Privacy',   Icon: IconShield,     path: '/privacy' },
  ]

  const active = (p) =>
    (p === '/' && (loc.pathname === '/' || loc.pathname === '/dashboard')) ||
    (p !== '/' && loc.pathname.startsWith(p))

  return (
    <nav
      className="glass-surface fixed bottom-0 left-0 right-0 z-30 sm:hidden"
      style={{ height: 56, borderTop: '0.5px solid rgba(255,255,255,0.55)' }}
      role="navigation"
      aria-label="Primary"
    >
      <div className="flex items-stretch h-full">
        {items.map(({ label, Icon, path }) => {
          const isActive = active(path)
          return (
            <button
              key={label}
              onClick={() => nav(path)}
              className="flex-1 flex flex-col items-center justify-center gap-0.5 relative transition-colors duration-150"
              style={{ color: isActive ? '#c8762a' : 'rgba(44,40,32,0.4)', fontSize: 9.5, fontWeight: 500 }}
            >
              {isActive && (
                <span
                  className="absolute top-0 left-1/2 -translate-x-1/2 rounded-b-full"
                  style={{ width: 24, height: 3, background: '#c8762a' }}
                />
              )}
              <Icon size={17} />
              <span>{label}</span>
            </button>
          )
        })}
      </div>
    </nav>
  )
}
