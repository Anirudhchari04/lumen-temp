import { useEffect, useState } from 'react'

// Listens to navigator.onLine + online/offline events and shows a thin banner
// when the browser is offline.
export default function OfflineBanner() {
  const [online, setOnline] = useState(
    typeof navigator !== 'undefined' ? navigator.onLine : true
  )

  useEffect(() => {
    const on = () => setOnline(true)
    const off = () => setOnline(false)
    window.addEventListener('online', on)
    window.addEventListener('offline', off)
    return () => {
      window.removeEventListener('online', on)
      window.removeEventListener('offline', off)
    }
  }, [])

  if (online) return null

  return (
    <div
      role="status"
      className="w-full bg-beige-200 text-ink-soft text-[12px] flex items-center justify-center gap-2 py-1.5"
    >
      <span
        className="w-[7px] h-[7px] rounded-full bg-amber"
        style={{ animation: 'lumen-pulse 1.1s ease-in-out infinite' }}
      />
      Offline — reconnecting…
    </div>
  )
}
