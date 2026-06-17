export default function QuickChips({ chips = [], onPick }) {
  if (!chips.length) return null
  return (
    <div
      className="flex items-center gap-1.5 px-5 py-2.5 overflow-x-auto no-scrollbar"
      style={{ borderTop: '0.5px solid rgba(0,0,0,0.05)' }}
    >
      {chips.map(c => (
        <button
          key={c}
          onClick={() => onPick?.(c)}
          className="glass-input shrink-0 rounded-full text-ink-soft hover:text-ink active:scale-95 transition-all duration-150 whitespace-nowrap"
          style={{ fontSize: 11.5, padding: '5px 13px', boxShadow: '0 1px 3px rgba(0,0,0,0.05)' }}
          onMouseEnter={e => { e.currentTarget.style.background = 'rgba(255,255,255,0.92)' }}
          onMouseLeave={e => { e.currentTarget.style.background = '' }}
        >
          {c}
        </button>
      ))}
    </div>
  )
}
