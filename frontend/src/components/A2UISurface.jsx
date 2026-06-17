import { useEffect, useRef } from 'react'
import { api } from '../lib/api.js'

/**
 * A2UISurface — React bridge component for the vanilla A2UI renderer.
 * Loads a2ui-renderer.js and calls window.renderA2UI() imperatively.
 * Button/form actions are sent to the structured /a2ui/action endpoint.
 */
export default function A2UISurface({ document: a2uiDoc, onAction, className = '' }) {
  const ref = useRef(null)

  const handleAction = (action) => {
    // If onAction is provided (e.g. sendLumen), use it for simple text actions
    if (onAction && typeof action === 'string' && !action.startsWith('{')) {
      onAction(action)
      return
    }
    // For structured actions, use the API
    try {
      const parsed = typeof action === 'string' ? JSON.parse(action) : action
      api.a2uiAction(parsed.action || action, parsed.data || {}, parsed.thread_id || '')
    } catch {
      if (onAction) onAction(action)
    }
  }

  useEffect(() => {
    if (!ref.current || !a2uiDoc) return

    // Ensure the renderer script is loaded
    if (!window.renderA2UI) {
      const script = document.createElement('script')
      script.src = '/js/a2ui-renderer.js'
      script.onload = () => {
        if (window.renderA2UI && ref.current) {
          window.renderA2UI(ref.current, a2uiDoc, handleAction)
        }
      }
      document.head.appendChild(script)
    } else {
      window.renderA2UI(ref.current, a2uiDoc, handleAction)
    }
  }, [a2uiDoc])

  if (!a2uiDoc) return null

  return <div ref={ref} className={`a2ui-surface ${className}`} data-surface={a2uiDoc?.surface || 'chat'} />
}
