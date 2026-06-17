import { useState, useEffect, useRef, useCallback } from 'react'

function WidgetCard({ widget, onRemove, onDragStart }) {
  const bodyRef = useRef(null)

  useEffect(() => {
    if (!bodyRef.current || !widget.a2ui) return
    if (!window.renderA2UI) {
      const s = document.createElement('script')
      s.src = '/js/a2ui-renderer.js'
      s.onload = () => window.renderA2UI?.(bodyRef.current, widget.a2ui)
      document.head.appendChild(s)
    } else {
      window.renderA2UI(bodyRef.current, widget.a2ui)
    }
  }, [widget.a2ui])

  return (
    <div
      className="bg-white rounded-xl flex flex-col"
      style={{
        resize: 'both', overflow: 'auto', minWidth: 200, minHeight: 120,
        border: '0.5px solid rgba(0,0,0,0.08)',
        boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
      }}
      data-widget-id={widget.id}
    >
      {/* Widget header */}
      <div
        className="px-3 py-2 flex items-center gap-2 select-none rounded-t-xl"
        style={{ borderBottom: '0.5px solid rgba(0,0,0,0.06)', background: '#faf8f4' }}
      >
        {/* Drag handle */}
        <span
          className="cursor-grab text-ink-muted/50 hover:text-ink-muted leading-none text-[10px] tracking-[2px]"
          onMouseDown={(e) => onDragStart(e, widget.id)}
          title="Drag to reorder"
        >
          ⠿
        </span>
        <span className="flex-1 text-[12px] font-medium text-ink truncate">{widget.title}</span>
        <button
          className="w-5 h-5 rounded-md flex items-center justify-center text-[10px] text-ink-muted hover:text-ink hover:bg-beige-200 transition-colors"
          onClick={() => onRemove(widget.id)}
          aria-label="Close widget"
        >
          ✕
        </button>
      </div>
      <div ref={bodyRef} className="flex-1 p-3 overflow-auto text-[13px]" />
    </div>
  )
}

export default function WidgetZone({ widgets = [], onRemove, onReorder }) {
  const [dragId, setDragId] = useState(null)
  const [overId, setOverId] = useState(null)
  const containerRef = useRef(null)

  const getWidgetFromPoint = useCallback((x, y) => {
    const els = containerRef.current?.children
    if (!els) return null
    for (const el of els) {
      const r = el.getBoundingClientRect()
      if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) {
        return el.dataset.widgetId
      }
    }
    return null
  }, [])

  const handleDragStart = useCallback((e, id) => {
    e.preventDefault()
    setDragId(id)

    const onMove = (ev) => {
      const target = getWidgetFromPoint(ev.clientX, ev.clientY)
      setOverId(target && target !== id ? target : null)
    }

    const onUp = (ev) => {
      const target = getWidgetFromPoint(ev.clientX, ev.clientY)
      if (target && target !== id && onReorder) {
        const order = widgets.map((w) => w.id)
        const from = order.indexOf(id)
        const to = order.indexOf(target)
        if (from !== -1 && to !== -1) {
          order.splice(from, 1)
          order.splice(to, 0, id)
          onReorder(order)
        }
      }
      setDragId(null)
      setOverId(null)
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }

    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [widgets, onReorder, getWidgetFromPoint])

  if (!widgets.length) {
    return (
      <div className="flex items-center justify-center h-full text-ink-muted text-sm px-6 text-center">
        Say &lsquo;add a clock&rsquo; or &lsquo;show a chart&rsquo; to add widgets here
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      className="grid grid-cols-1 md:grid-cols-2 gap-3 p-3"
    >
      {widgets.map((w) => (
        <div
          key={w.id}
          data-widget-id={w.id}
          className={[
            'transition-opacity',
            dragId === w.id ? 'opacity-50' : '',
            overId === w.id ? 'ring-2 ring-beige-300 rounded-lg' : '',
          ].join(' ')}
        >
          <WidgetCard
            widget={w}
            onRemove={onRemove}
            onDragStart={handleDragStart}
          />
        </div>
      ))}
    </div>
  )
}
