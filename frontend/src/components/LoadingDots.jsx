// Three amber dots, staggered pulse — used while awaiting a Lumen/TA reply.
export default function LoadingDots({ label = 'Thinking' }) {
  return (
    <div className="flex items-center gap-2 px-3 py-2 rounded-card bg-white border border-beige-200 w-fit"
         role="status" aria-label={label}>
      <div className="flex items-center gap-1">
        <Dot delay="0ms" />
        <Dot delay="160ms" />
        <Dot delay="320ms" />
      </div>
      {label && <span className="text-[11px] text-ink-muted">{label}</span>}
    </div>
  )
}

function Dot({ delay }) {
  return (
    <span
      className="w-1.5 h-1.5 rounded-full bg-amber"
      style={{
        animation: 'lumen-pulse 1.1s ease-in-out infinite',
        animationDelay: delay,
      }}
    />
  )
}
