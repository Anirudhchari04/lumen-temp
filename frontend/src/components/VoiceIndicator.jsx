/**
 * VoiceIndicator — floating orb that shows voice state.
 * Sits at the bottom-center of the screen without covering the chat.
 */

import { useEffect, useRef } from 'react'

const STATE_CONFIG = {
  idle:       { label: 'Tap to start',    color: '#c4a97d', glow: 'rgba(196,169,125,0.35)', pulse: false },
  connecting: { label: 'Connecting…',     color: '#8b5cf6', glow: 'rgba(139,92,246,0.45)',  pulse: true  },
  listening:  { label: 'Listening…',      color: '#3b82f6', glow: 'rgba(59,130,246,0.45)',  pulse: true  },
  thinking:   { label: 'Thinking…',       color: '#f59e0b', glow: 'rgba(245,158,11,0.45)',  pulse: true  },
  speaking:   { label: 'Lumen speaking',  color: '#10b981', glow: 'rgba(16,185,129,0.45)',  pulse: true  },
  error:      { label: 'Voice unavailable', color: '#ef4444', glow: 'rgba(239,68,68,0.35)', pulse: false },
}

export default function VoiceIndicator({ voiceState, transcript, lumenText, errorMsg, onClose, bargeIn, onReconnect }) {
  const cfg = STATE_CONFIG[voiceState] || STATE_CONFIG.idle
  const isActive = voiceState !== 'idle'

  const timerRef = useRef(null)
  useEffect(() => {
    if (voiceState === 'error') {
      timerRef.current = setTimeout(() => onClose?.(), 8000)
    }
    return () => clearTimeout(timerRef.current)
  }, [voiceState, onClose])

  if (!isActive) return null

  return (
    <div
      className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 flex flex-col items-center gap-2.5 pointer-events-none"
      style={{ minWidth: 280, maxWidth: 500 }}
    >
      {/* Error panel */}
      {voiceState === 'error' && errorMsg && (
        <div className="pointer-events-auto w-full bg-red-950/95 backdrop-blur-md text-white rounded-2xl px-4 py-3 text-[12px] leading-relaxed shadow-2xl border border-red-400/20 flex items-start gap-3">
          <div className="flex-1 min-w-0">
            <span className="block text-[9.5px] text-red-300/70 mb-1 uppercase tracking-[0.12em] font-medium">Voice Error</span>
            <span className="text-red-100 text-[12.5px]">{errorMsg}</span>
          </div>
          {onReconnect && (
            <button
              onClick={onReconnect}
              className="shrink-0 self-center text-[11px] text-red-200 hover:text-white border border-red-400/30 hover:border-white/50 rounded-full px-3 py-1 transition-all duration-150"
            >
              Reconnect
            </button>
          )}
        </div>
      )}

      {/* Transcript / response bubble */}
      {!errorMsg && (transcript || lumenText) && (
        <div className="pointer-events-auto w-full bg-[#161622]/92 backdrop-blur-md text-white rounded-2xl px-4 py-3 text-[13px] leading-relaxed shadow-2xl border border-white/[0.08]">
          {lumenText ? (
            <>
              <span className="block text-[9.5px] text-emerald-400/70 mb-1 uppercase tracking-[0.12em] font-medium">Lumen</span>
              <span className="text-white/90">{lumenText}</span>
            </>
          ) : (
            <>
              <span className="block text-[9.5px] text-blue-400/70 mb-1 uppercase tracking-[0.12em] font-medium">You</span>
              <span className="text-white/75 italic">{transcript}</span>
            </>
          )}
        </div>
      )}

      {/* Control pill */}
      <div className="pointer-events-auto flex items-center gap-3 bg-[#161622]/92 backdrop-blur-md rounded-full px-4 py-2.5 shadow-2xl border border-white/[0.08]">
        {/* Animated orb */}
        <div className="relative flex items-center justify-center shrink-0">
          {cfg.pulse && (
            <div
              className="absolute rounded-full animate-ping opacity-60"
              style={{ width: 30, height: 30, backgroundColor: cfg.glow }}
            />
          )}
          <div
            className="relative rounded-full transition-colors duration-500"
            style={{ width: 20, height: 20, backgroundColor: cfg.color, boxShadow: `0 0 14px ${cfg.glow}` }}
          />
        </div>

        {/* State label */}
        <span className="text-white/75 text-[13px] font-medium min-w-[92px] tracking-[-0.01em]">
          {cfg.label}
        </span>

        {/* Barge-in */}
        {voiceState === 'speaking' && (
          <button
            onClick={bargeIn}
            className="text-[11px] text-amber-300/90 hover:text-amber-200 transition-colors border border-amber-500/25 hover:border-amber-400/50 rounded-full px-2.5 py-0.5"
            title="Interrupt Lumen"
          >
            Interrupt
          </button>
        )}

        {/* Close */}
        <button
          onClick={onClose}
          className="text-white/30 hover:text-white/70 transition-colors ml-0.5 text-[14px] leading-none"
          title="Stop voice"
          aria-label="Stop voice"
        >
          ✕
        </button>
      </div>
    </div>
  )
}
