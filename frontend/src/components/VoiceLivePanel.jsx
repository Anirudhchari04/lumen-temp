/**
 * VoiceLivePanel — animated voice interface like the Foundry demo.
 * Shows pulsing orb + live transcript + streaming Lumen response.
 */
import { useEffect, useRef } from 'react'

const STATE_CONFIG = {
  idle:       { label: 'Tap to start voice',   color: 'bg-beige-300',   pulse: false, ring: 'ring-beige-200' },
  connecting: { label: 'Connecting…',           color: 'bg-violet-500',  pulse: true,  ring: 'ring-violet-200' },
  listening:  { label: 'Listening…',            color: 'bg-blue-500',    pulse: true,  ring: 'ring-blue-200' },
  thinking:   { label: 'Thinking…',             color: 'bg-amber',       pulse: true,  ring: 'ring-amber/30' },
  speaking:   { label: 'Lumen is speaking',     color: 'bg-emerald-500', pulse: true,  ring: 'ring-emerald-200' },
}

export default function VoiceLivePanel({
  voiceState, transcript, lumenText, activate, bargeIn, onClose, messages = []
}) {
  const cfg = STATE_CONFIG[voiceState] || STATE_CONFIG.idle
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length, lumenText])

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-[#1a1a2e] text-white">
      {/* Header */}
      <div className="flex items-center justify-between px-5 pt-5 pb-3">
        <div className="flex items-center gap-2">
          <span className="text-amber font-semibold text-[15px]">✦ Lumen</span>
          <span className="text-[11px] text-white/40 uppercase tracking-wider">Voice Live</span>
        </div>
        <button
          onClick={onClose}
          className="text-white/40 hover:text-white/80 text-[13px] transition-colors"
        >
          ✕ Exit voice
        </button>
      </div>

      {/* Conversation transcript area */}
      <div className="flex-1 min-h-0 overflow-y-auto px-5 py-2 flex flex-col gap-3">
        {messages.map((m, i) => (
          <div key={m.id || i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[78%] rounded-2xl px-4 py-2.5 text-[13.5px] leading-relaxed ${
              m.role === 'user'
                ? 'bg-white/10 text-white/90'
                : 'bg-amber/20 text-amber-100'
            }`}>
              {m.role === 'lumen' && (
                <span className="block text-[10px] text-amber/60 mb-1 uppercase tracking-widest">Lumen</span>
              )}
              {m.content}
            </div>
          </div>
        ))}

        {/* Streaming response preview */}
        {lumenText && (
          <div className="flex justify-start">
            <div className="max-w-[78%] rounded-2xl px-4 py-2.5 text-[13.5px] leading-relaxed bg-amber/20 text-amber-100">
              <span className="block text-[10px] text-amber/60 mb-1 uppercase tracking-widest">Lumen</span>
              {lumenText}
              <span className="inline-block w-1.5 h-3.5 bg-amber/60 ml-0.5 animate-pulse align-middle" />
            </div>
          </div>
        )}

        {/* Live transcript */}
        {transcript && (
          <div className="flex justify-end">
            <div className="max-w-[78%] rounded-2xl px-4 py-2.5 text-[13.5px] leading-relaxed bg-white/5 text-white/50 italic">
              {transcript}…
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Central orb + status */}
      <div className="flex flex-col items-center py-8 gap-4">
        {/* Animated orb */}
        <button
          onClick={voiceState === 'speaking' ? bargeIn : activate}
          className="relative flex items-center justify-center w-24 h-24 rounded-full focus:outline-none"
          title={voiceState === 'speaking' ? 'Tap to interrupt' : voiceState === 'idle' ? 'Tap to start' : ''}
        >
          {/* Outer pulse ring */}
          {cfg.pulse && (
            <>
              <span className={`absolute inset-0 rounded-full ${cfg.color} opacity-20 animate-ping`} />
              <span className={`absolute inset-2 rounded-full ${cfg.color} opacity-30 animate-ping [animation-delay:150ms]`} />
            </>
          )}
          {/* Ring */}
          <span className={`absolute inset-0 rounded-full ring-4 ${cfg.ring}`} />
          {/* Core orb */}
          <span className={`relative w-16 h-16 rounded-full ${cfg.color} flex items-center justify-center shadow-lg`}>
            {voiceState === 'idle'     && <span className="text-2xl">🎙️</span>}
            {voiceState === 'listening'&& <SoundWave />}
            {voiceState === 'thinking' && <ThinkingDots />}
            {voiceState === 'speaking' && <span className="text-white text-[11px] font-medium">tap to<br/>interrupt</span>}
          </span>
        </button>

        {/* State label */}
        <p className="text-[13px] text-white/60 font-medium">{cfg.label}</p>

        {/* Stop button */}
        {voiceState !== 'idle' && (
          <button
            onClick={activate}
            className="mt-1 text-[11px] text-white/30 hover:text-white/60 transition-colors underline underline-offset-2"
          >
            Say "stop" or tap here to exit
          </button>
        )}
      </div>
    </div>
  )
}

function SoundWave() {
  return (
    <div className="flex items-center gap-0.5 h-6">
      {[3, 5, 8, 5, 3].map((h, i) => (
        <span
          key={i}
          className="w-1 bg-white rounded-full animate-pulse"
          style={{ height: `${h * 2}px`, animationDelay: `${i * 100}ms` }}
        />
      ))}
    </div>
  )
}

function ThinkingDots() {
  return (
    <div className="flex items-center gap-1">
      {[0, 150, 300].map((delay, i) => (
        <span
          key={i}
          className="w-2 h-2 bg-white rounded-full animate-bounce"
          style={{ animationDelay: `${delay}ms` }}
        />
      ))}
    </div>
  )
}
