import { useState } from 'react'
import NotificationsBell from './NotificationsBell.jsx'
import ProfileModal from './ProfileModal.jsx'
import { IconSun, IconMoon } from './icons.jsx'

function formatDate(d = new Date()) {
  return d.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' })
}

function greeting() {
  const h = new Date().getHours()
  if (h < 12) return 'Good morning'
  if (h < 17) return 'Good afternoon'
  return 'Good evening'
}

export default function TopStrip({ userName = 'there', userInitials = 'U', eventCount = 0, isDark, onToggleTheme }) {
  const [showProfile, setShowProfile] = useState(false)

  return (
    <>
      <header
        className="flex items-center justify-between px-6 pt-5 pb-4 shrink-0"
        style={{ borderBottom: '0.5px solid var(--border-light)' }}
      >
        {/* Greeting */}
        <div className="flex flex-col gap-0.5">
          <h1
            className="leading-snug"
            style={{ fontSize: 18, fontWeight: 600, color: 'var(--text-ink)', letterSpacing: '-0.015em' }}
          >
            {greeting()},{' '}
            <span style={{ color: '#c8762a' }}>{userName}</span>
            <span style={{ color: 'rgba(200,118,42,0.50)', fontSize: 13, marginLeft: 6 }}>✦</span>
          </h1>
          <p style={{ fontSize: 11.5, color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}>
            {formatDate()}
            {eventCount > 0 && (
              <>
                <span style={{ margin: '0 6px', opacity: 0.4 }}>·</span>
                <span>{eventCount} event{eventCount === 1 ? '' : 's'} today</span>
              </>
            )}
          </p>
        </div>

        {/* Profile cluster — only here, not in the rail */}
        <div className="flex items-center gap-1.5">
          {/* Theme toggle */}
          {onToggleTheme && (
            <button
              onClick={onToggleTheme}
              aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
              title={isDark ? 'Light mode' : 'Dark mode'}
              className="w-8 h-8 rounded-xl flex items-center justify-center transition-all duration-150"
              style={{ color: 'var(--text-soft)', background: 'transparent' }}
              onMouseEnter={e => { e.currentTarget.style.background = 'var(--border-light)' }}
              onMouseLeave={e => { e.currentTarget.style.background = 'transparent' }}
            >
              {isDark ? <IconSun size={15} /> : <IconMoon size={15} />}
            </button>
          )}

          {/* Real notifications bell — self-manages its dropdown */}
          <NotificationsBell />

          {/* Avatar → opens profile modal */}
          <button
            onClick={() => setShowProfile(true)}
            className="rounded-full flex items-center justify-center font-semibold transition-all duration-150 active:scale-95"
            style={{
              width: 32, height: 32,
              background: '#c8762a',
              color: 'white',
              fontSize: 11,
              boxShadow: '0 2px 8px rgba(200,118,42,0.30)',
              outline: '2px solid rgba(200,118,42,0.18)',
              outlineOffset: 1,
            }}
            aria-label="Your profile"
            title="Edit profile"
          >
            {userInitials}
          </button>
        </div>
      </header>

      <ProfileModal
        open={showProfile}
        onClose={() => setShowProfile(false)}
        onSaved={() => setShowProfile(false)}
      />
    </>
  )
}
