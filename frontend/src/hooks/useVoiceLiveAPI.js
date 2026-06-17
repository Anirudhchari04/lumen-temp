/**
 * useVoiceLiveAPI — Real Azure AI Voice Live API integration.
 *
 * Architecture:
 *   Browser mic → PCM16 base64 → WebSocket → backend proxy
 *   → Azure Voice Live API → audio delta → WebSocket → browser → Web Audio playback
 *
 * The backend proxy (/lumen/voice-live-ws) fetches the AAD token and connects
 * to wss://anirfoundry.services.ai.azure.com/voice-live/realtime with the
 * voice-live-lumen agent, which has Lumen's system prompt pre-configured.
 *
 * States: 'idle' | 'connecting' | 'listening' | 'thinking' | 'speaking'
 */

import { useCallback, useEffect, useRef, useState } from 'react'

const TARGET_SAMPLE_RATE = 24000 // Voice Live API input sample rate

// ── PCM16 helpers ─────────────────────────────────────────────────────────

/** Resample Float32 audio from sourceSR to targetSR (linear interpolation). */
function resample(float32, sourceSR, targetSR) {
  if (sourceSR === targetSR) return float32
  const ratio = sourceSR / targetSR
  const outLen = Math.round(float32.length / ratio)
  const out = new Float32Array(outLen)
  for (let i = 0; i < outLen; i++) {
    const src = i * ratio
    const idx = Math.floor(src)
    const frac = src - idx
    const a = float32[idx] ?? 0
    const b = float32[idx + 1] ?? a
    out[i] = a + frac * (b - a)
  }
  return out
}

/** Float32 → PCM16 → base64. */
function encodePCM16Base64(float32) {
  const pcm16 = new Int16Array(float32.length)
  for (let i = 0; i < float32.length; i++) {
    pcm16[i] = Math.max(-32768, Math.min(32767, float32[i] * 32768))
  }
  const bytes = new Uint8Array(pcm16.buffer)
  let bin = ''
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
  return btoa(bin)
}

/** base64 PCM16 → Float32. */
function decodePCM16Base64(b64) {
  const bin = atob(b64)
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
  const pcm16 = new Int16Array(bytes.buffer)
  const float32 = new Float32Array(pcm16.length)
  for (let i = 0; i < pcm16.length; i++) float32[i] = pcm16[i] / 32768
  return float32
}

// ── Main hook ─────────────────────────────────────────────────────────────

export default function useVoiceLiveAPI({ onMessage, onStateChange, onError } = {}) {
  const [voiceState, setVoiceState] = useState('idle')
  const [transcript, setTranscript]  = useState('')
  const [lumenText, setLumenText]    = useState('')
  const [errorMsg, setErrorMsg]      = useState('')

  const wsRef         = useRef(null)
  const audioCtxRef   = useRef(null)  // for recording (mic)
  const playCtxRef    = useRef(null)  // for playback (speaker)
  const processorRef  = useRef(null)
  const streamRef     = useRef(null)
  const playNextRef   = useRef(0)     // scheduled playback time
  const activeRef     = useRef(false)
  const sessionReadyRef = useRef(false) // mic started flag (idempotent)
  const connectTimerRef = useRef(null)  // safety timeout for stuck-connecting
  // Mirror voiceState into a ref so the WS onmessage closure can read the
  // current value (the closure is created once at activate() time, so without
  // a ref the speech_started→bargeIn check would always see stale state).
  const voiceStateRef = useRef('idle')

  const updateState = useCallback((s) => {
    voiceStateRef.current = s
    setVoiceState(s)
    onStateChange?.(s)
  }, [onStateChange])

  const showError = useCallback((msg) => {
    setErrorMsg(msg)
    updateState('error')
    onError?.(msg)
    // Auto-reset to idle after 3s (VoiceIndicator also auto-dismisses)
    setTimeout(() => {
      setErrorMsg('')
      updateState('idle')
    }, 3500)
  }, [updateState, onError])

  // ── Playback: queue a 24kHz PCM16 base64 chunk ──────────────────────
  const playChunk = useCallback((b64) => {
    if (!playCtxRef.current) return
    try {
      const float32 = decodePCM16Base64(b64)
      const buf = playCtxRef.current.createBuffer(1, float32.length, TARGET_SAMPLE_RATE)
      buf.copyToChannel(float32, 0)
      const src = playCtxRef.current.createBufferSource()
      src.buffer = buf
      src.connect(playCtxRef.current.destination)
      const now = playCtxRef.current.currentTime
      const start = Math.max(now, playNextRef.current)
      src.start(start)
      playNextRef.current = start + buf.duration
      updateState('speaking')
    } catch (e) {
      console.warn('Voice Live playback error:', e)
    }
  }, [updateState])

  // ── Stop mic capture + AudioContext ──────────────────────────────────
  const stopMic = useCallback(() => {
    try { processorRef.current?.disconnect() } catch {}
    try { processorRef.current?.onaudioprocess && (processorRef.current.onaudioprocess = null) } catch {}
    processorRef.current = null
    streamRef.current?.getTracks().forEach(t => t.stop())
    streamRef.current = null
    audioCtxRef.current?.close().catch(() => {})
    audioCtxRef.current = null
  }, [])

  // ── Stop everything ──────────────────────────────────────────────────
  const deactivate = useCallback(() => {
    activeRef.current = false
    sessionReadyRef.current = false
    if (connectTimerRef.current) { clearTimeout(connectTimerRef.current); connectTimerRef.current = null }
    stopMic()
    playCtxRef.current?.close().catch(() => {})
    playCtxRef.current = null
    playNextRef.current = 0
    if (wsRef.current) {
      wsRef.current.onclose = null
      wsRef.current.close()
      wsRef.current = null
    }
    updateState('idle')
    setTranscript('')
    setLumenText('')
  }, [stopMic, updateState])

  // ── Barge-in: cancel Lumen's current response ────────────────────────
  const bargeIn = useCallback(() => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return
    wsRef.current.send(JSON.stringify({ type: 'response.cancel' }))
    // Clear scheduled audio
    playCtxRef.current?.close().catch(() => {})
    playCtxRef.current = new AudioContext()
    playNextRef.current = 0
    updateState('listening')
  }, [updateState])

  // ── Start microphone capture ─────────────────────────────────────────
  const startMic = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      })
      streamRef.current = stream

      audioCtxRef.current = new AudioContext()
      const sourceSR = audioCtxRef.current.sampleRate
      const source = audioCtxRef.current.createMediaStreamSource(stream)

      // ScriptProcessorNode: 2048 samples per chunk (~43ms at 48kHz) — lower latency than 4096
      const processor = audioCtxRef.current.createScriptProcessor(2048, 1, 1)
      processorRef.current = processor

      processor.onaudioprocess = (e) => {
        if (!activeRef.current || wsRef.current?.readyState !== WebSocket.OPEN) return
        const raw = e.inputBuffer.getChannelData(0)
        const resampled = resample(raw, sourceSR, TARGET_SAMPLE_RATE)
        const b64 = encodePCM16Base64(resampled)
        wsRef.current.send(JSON.stringify({ type: 'input_audio_buffer.append', audio: b64 }))
      }

      source.connect(processor)
      processor.connect(audioCtxRef.current.destination)
    } catch (err) {
      console.error('Mic access failed:', err)
      updateState('idle')
    }
  }, [updateState])

  // ── Activate voice live ──────────────────────────────────────────────
  const activate = useCallback(async () => {
    // Toggle off if already active
    if (activeRef.current) { deactivate(); return }

    activeRef.current = true
    sessionReadyRef.current = false
    updateState('connecting')

    const authToken = localStorage.getItem('lumen.token') || localStorage.getItem('lumen_token') || ''

    // Acquire the user's own Azure AI Foundry token so the backend proxy
    // can use it directly — bypasses managed-identity role assignment.
    let foundryToken = ''
    try {
      const { getAIFoundryToken } = await import('../lib/auth.js')
      foundryToken = await getAIFoundryToken()
    } catch (e) {
      console.warn('Voice Live: could not get AI Foundry token, falling back to managed identity:', e)
    }

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ftParam = foundryToken ? `&foundry_token=${encodeURIComponent(foundryToken)}` : ''
    const wsUrl   = `${proto}//${location.host}/lumen/voice-live-ws?token=${encodeURIComponent(authToken)}${ftParam}`

    playCtxRef.current = new AudioContext()
    playNextRef.current = 0

    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    // Idempotent helper — start mic + transition to listening exactly once.
    const beginListening = () => {
      if (sessionReadyRef.current || !activeRef.current) return
      sessionReadyRef.current = true
      if (connectTimerRef.current) { clearTimeout(connectTimerRef.current); connectTimerRef.current = null }
      console.info('Voice Live: session ready — starting mic.')
      startMic().then(() => updateState('listening'))
    }

    ws.onopen = async () => {
      if (!activeRef.current) { ws.close(); return }

      // Minimal session.update — only configure VAD and audio formats. The
      // voice-live-lumen agent already has its own voice/system-prompt/transcription
      // baked in, so we don't override those (overrides get rejected by some
      // api-versions and cause the connection to silently stall).
      ws.send(JSON.stringify({
        type: 'session.update',
        session: {
          turn_detection: {
            type: 'server_vad',
            silence_duration_ms: 700,
            threshold: 0.5,
            prefix_padding_ms: 200,
          },
          input_audio_format: 'pcm16',
          output_audio_format: 'pcm16',
        },
      }))

      // Safety net — if neither session.created nor session.updated arrives
      // within 8s, surface an error so the user isn't stuck on "Connecting…".
      connectTimerRef.current = setTimeout(() => {
        if (!sessionReadyRef.current && activeRef.current) {
          console.warn('Voice Live: session not ready after 8s — aborting.')
          showError('Voice Live did not respond. Try Reconnect or use text mode.')
          deactivate()
        }
      }, 8000)
    }

    ws.onmessage = (e) => {
      let msg
      try { msg = JSON.parse(e.data) } catch { return }

      switch (msg.type) {
        case 'session.created':
          // Agent is ready. Some api-versions never send session.updated for
          // agent-mode connections — start the mic now and proceed.
          beginListening()
          break

        case 'session.updated':
          // Session config confirmed — start mic if we haven't already.
          beginListening()
          break

        case 'input_audio_buffer.speech_started':
          setTranscript('')
          // If Lumen was speaking, barge in (read via ref — voiceState here
          // is captured by the activate() closure and would always be stale).
          if (voiceStateRef.current === 'speaking') bargeIn()
          updateState('listening')
          break

        case 'input_audio_buffer.speech_stopped':
          updateState('thinking')
          break

        case 'conversation.item.input_audio_transcription.completed':
          if (msg.transcript) {
            setTranscript(msg.transcript)
            onMessage?.({ role: 'user', content: msg.transcript, id: Date.now() + '-u' })
            setTimeout(() => setTranscript(''), 3000)
          }
          break

        case 'response.created':
          updateState('thinking')
          setLumenText('')
          break

        case 'response.audio.delta':
          if (msg.delta) playChunk(msg.delta)
          break

        case 'response.audio_transcript.delta':
          setLumenText(prev => prev + (msg.delta || ''))
          break

        case 'response.audio_transcript.done':
          if (msg.transcript) {
            onMessage?.({ role: 'lumen', content: msg.transcript, id: Date.now() + '-l' })
          }
          setLumenText('')
          updateState('listening')
          break

        case 'response.done':
          updateState('listening')
          break

        case 'error':
          console.error('Voice Live API error:', JSON.stringify(msg.error))
          showError(msg.error?.message || 'Voice Live connection failed — falling back to text mode.')
          deactivate()
          break

        default:
          // Log unknown message types so we can debug agent-mode quirks.
          if (msg?.type) console.debug('Voice Live msg:', msg.type)
          break
      }
    }

    ws.onclose = (evt) => {
      if (connectTimerRef.current) { clearTimeout(connectTimerRef.current); connectTimerRef.current = null }
      if (activeRef.current) {
        activeRef.current = false
        sessionReadyRef.current = false
        stopMic()
        // Surface error for any unexpected close (connecting OR listening/thinking/speaking)
        setVoiceState(prev => {
          if (prev !== 'idle' && prev !== 'error') {
            const reason = evt.reason ? ` (${evt.reason})` : ''
            const errMsg = prev === 'connecting'
              ? `Could not connect to Voice Live API${reason}.`
              : `Voice Live disconnected unexpectedly${reason}. Code: ${evt.code}`
            console.warn('Voice Live closed:', evt.code, evt.reason, 'from state:', prev)
            setErrorMsg(errMsg)
            onError?.(errMsg)
            setTimeout(() => { setErrorMsg(''); setVoiceState('idle') }, 4000)
            return 'error'
          }
          return 'idle'
        })
      }
    }

    ws.onerror = (err) => {
      console.error('Voice Live WebSocket error:', err)
      console.error('Voice Live WS URL:', wsUrl)
      if (connectTimerRef.current) { clearTimeout(connectTimerRef.current); connectTimerRef.current = null }
      activeRef.current = false
      sessionReadyRef.current = false
      stopMic()
      showError('Voice Live connection error — check console for details.')
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deactivate, startMic, playChunk, bargeIn, updateState, onMessage, stopMic])

  useEffect(() => () => {
    activeRef.current = false
    stopMic()
    wsRef.current?.close()
    playCtxRef.current?.close().catch(() => {})
  }, [stopMic])

  return {
    voiceState,
    transcript,
    lumenText,
    errorMsg,
    activate,
    deactivate,
    bargeIn,
    isActive: voiceState !== 'idle',
  }
}
