import OfflineBanner from './OfflineBanner.jsx'

// Outer shell — transparent so the warm page background shows through glass panels.
export default function Shell({ children, rail, bottomNav }) {
  return (
    <div className="min-h-screen p-0 md:p-3" style={{ background: 'var(--bg-page)' }}>
      <OfflineBanner />
      <div
        className="overflow-hidden min-h-[calc(100vh-24px)] flex pb-[52px] sm:pb-0"
        style={{
          borderRadius: 18,
          border: '0.5px solid var(--border-glass)',
          boxShadow: '0 8px 32px rgba(0,0,0,0.10)',
        }}
      >
        <div className="hidden sm:flex">{rail}</div>
        <div className="flex-1 min-w-0 flex" style={{ background: 'var(--bg-main)' }}>
          {children}
        </div>
      </div>
      {bottomNav}
    </div>
  )
}
