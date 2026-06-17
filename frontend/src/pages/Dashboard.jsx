import { useEffect, useState } from 'react'
import TopStrip from '../components/TopStrip.jsx'
import LumenChat from '../components/LumenChat.jsx'
import LumenThreadSidebar from '../components/LumenThreadSidebar.jsx'
import WidgetZone from '../components/WidgetZone.jsx'
import { api } from '../lib/api.js'

export default function Dashboard({ session, user, isDark, onToggleTheme }) {
  const { lumenMessages, sendLumen, lumenTyping, notifications, confirmProposal,
          lumenThreadId, loadThread, newThread, onSpeakEndRef } = session
  const unreadCount = notifications.filter(n => !n.read).length
  const [widgets, setWidgets] = useState([])
  const [showSidebar, setShowSidebar] = useState(false)

  const name = user?.name?.split(' ')[0] || 'there'
  const initials = (user?.name || user?.email || 'U')
    .split(/[\s@._-]+/).filter(Boolean).slice(0, 2)
    .map(s => s[0]?.toUpperCase()).join('') || 'U'

  useEffect(() => {
    api.widgetsGet().then(r => setWidgets(r?.widgets || [])).catch(() => {})
  }, [])

  useEffect(() => {
    const last = lumenMessages[lumenMessages.length - 1]
    if (!last || last.role !== 'lumen') return
    if (last.action === 'widget_added' || last.action === 'widget_removed') {
      api.widgetsGet().then(r => setWidgets(r?.widgets || [])).catch(() => {})
    }
    if (last.action === 'font_scale_up') {
      const cur = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
      document.documentElement.style.fontSize = (cur + 2) + 'px'
    }
    if (last.action === 'font_scale_down') {
      const cur = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
      document.documentElement.style.fontSize = Math.max(12, cur - 2) + 'px'
    }
  }, [lumenMessages.length])

  const handleRemoveWidget = async (id) => {
    await api.widgetRemove(id)
    setWidgets(w => w.filter(x => x.id !== id))
  }

  return (
    <div className="flex-1 min-w-0 flex flex-col">
      <TopStrip userName={name} userInitials={initials} eventCount={0} isDark={isDark} onToggleTheme={onToggleTheme} />

      {/* Subtle full-width divider */}
      <div className="h-px mx-0 bg-gradient-to-r from-transparent via-beige-200 to-transparent shrink-0" />

      {/* Widget zone — only shown when there are widgets */}
      {widgets.length > 0 && (
        <div className="px-5 py-3" style={{ borderBottom: '0.5px solid rgba(0,0,0,0.06)' }}>
          <WidgetZone
            widgets={widgets}
            onRemove={handleRemoveWidget}
            onReorder={(newOrder) => setWidgets(newOrder)}
          />
        </div>
      )}

      <div className="flex flex-1 min-h-0">
        {showSidebar && (
          <LumenThreadSidebar
            activeThreadId={lumenThreadId}
            onSelectThread={(id) => loadThread(id)}
            onNewThread={newThread}
          />
        )}

        <div className="flex-1 min-w-0 flex flex-col">
          {/* Chat sub-header */}
          <div className="px-5 py-2 flex items-center gap-3 shrink-0" style={{ borderBottom: '0.5px solid rgba(0,0,0,0.06)' }}>
            {/* Sidebar toggle */}
            <button
              onClick={() => setShowSidebar(s => !s)}
              title={showSidebar ? 'Hide history' : 'Show history'}
              aria-label={showSidebar ? 'Hide history' : 'Show history'}
              className={`flex items-center gap-1.5 text-[11.5px] px-2.5 py-1 rounded-lg border transition-all duration-150 ${
                showSidebar
                  ? 'bg-beige-200 text-ink border-beige-300'
                  : 'bg-transparent text-ink-muted border-transparent hover:bg-beige-100 hover:text-ink hover:border-beige-200'
              }`}
            >
              <svg width="13" height="13" viewBox="0 0 13 13" fill="none" aria-hidden>
                <rect x="1" y="1" width="3.5" height="11" rx="1" fill="currentColor" opacity={showSidebar ? '0.8' : '0.4'} />
                <rect x="6" y="1" width="6" height="2.5" rx="0.75" fill="currentColor" opacity="0.4" />
                <rect x="6" y="5.25" width="6" height="2.5" rx="0.75" fill="currentColor" opacity="0.4" />
                <rect x="6" y="9.5" width="6" height="2.5" rx="0.75" fill="currentColor" opacity="0.4" />
              </svg>
              <span>History</span>
            </button>

            {/* Context hint */}
            <p className="text-[11px] text-ink-muted/70 flex-1 truncate hidden sm:block">
              Ask Lumen anything — or say "open math TA" to launch an agent
            </p>
          </div>

          <LumenChat
            messages={lumenMessages}
            onSend={sendLumen}
            typing={lumenTyping}
            onConfirmProposal={confirmProposal}
            uxPreset={session.uxPreset}
            onSpeakEndRef={onSpeakEndRef}
          />
        </div>
      </div>
    </div>
  )
}
