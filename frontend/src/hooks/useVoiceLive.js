/**
 * useVoiceLive — fluid voice loop like the Foundry demo.
 *
 * Flow:
 *   activate → always-on VAD (continuous recognition) → user speaks →
 *   speech detected → stream LLM response → TTS sentence-by-sentence →
 *   VAD restarts automatically — barge-in cancels TTS mid-sentence
 *
 * States: 'idle' | 'listening' | 'thinking' | 'speaking'
 */

import { useCallback, useEffect, useRef, useState } from 'react'

// ── Token cache ───────────────────────────────────────────────
let _tokenCache = null
let _tokenExpiry = 0

async function fetchSpeechToken() {
  if (_tokenCache && Date.now() < _tokenExpiry) return _tokenCache
  try {
    const stored = localStorage.getItem('lumen.token') || localStorage.getItem('lumen_token')
    const resp = await fetch('/lumen/speech-token', {
      headers: { Authorization: `Bearer ${stored}` },
    })
    if (!resp.ok) return null
    const data = await resp.json()
    if (data.available && data.token) {
      _tokenCache = data
      _tokenExpiry = Date.now() + 8 * 60 * 1000
      return data
    }
    return null
  } catch { return null }
}

// ── Sentence splitter for streaming TTS ──────────────────────
function splitSentences(text) {
  return text.match(/[^.!?…]+[.!?…]+|[^.!?…]+$/g)?.map(s => s.trim()).filter(Boolean) || [text.trim()]
}

// ── Azure TTS (streaming, sentence-by-sentence) ──────────────
let _activeSynth = null

async function speakSentence(text, tokenData, { onStart, onEnd, signal } = {}) {
  if (signal?.aborted || !text.trim()) { onEnd?.(); return }
  const clean = text.replace(/[*#_`]/g, '').trim()
  if (!clean) { onEnd?.(); return }

  onStart?.()

  if (tokenData?.available && tokenData?.token) {
    try {
      const sdk = await import('microsoft-cognitiveservices-speech-sdk')
      if (signal?.aborted) { onEnd?.(); return }

      const cfg = sdk.SpeechConfig.fromAuthorizationToken(tokenData.token, tokenData.region || 'eastus')
      cfg.speechSynthesisVoiceName = 'en-IN-AaravNeural'
      const synth = new sdk.SpeechSynthesizer(cfg)
      _activeSynth = synth

      return new Promise((resolve) => {
        const abort = () => { try { synth.close() } catch {} _activeSynth = null; onEnd?.(); resolve() }
        signal?.addEventListener('abort', abort, { once: true })

        synth.speakTextAsync(clean,
          () => { signal?.removeEventListener('abort', abort); synth.close(); _activeSynth = null; onEnd?.(); resolve() },
          () => { signal?.removeEventListener('abort', abort); synth.close(); _activeSynth = null; _browserSpeak(clean, onEnd, signal); resolve() }
        )
      })
    } catch {}
  }
  return _browserSpeak(clean, onEnd, signal)
}

function _browserSpeak(text, onEnd, signal) {
  return new Promise((resolve) => {
    if (!window.speechSynthesis || signal?.aborted) { onEnd?.(); resolve(); return }
    window.speechSynthesis.cancel()
    const utt = new SpeechSynthesisUtterance(text)
    utt.rate = 1.05; utt.lang = 'en-US'
    const abort = () => { window.speechSynthesis.cancel(); onEnd?.(); resolve() }
    signal?.addEventListener('abort', abort, { once: true })
    utt.onend = () => { signal?.removeEventListener('abort', abort); onEnd?.(); resolve() }
    utt.onerror = () => { signal?.removeEventListener('abort', abort); onEnd?.(); resolve() }
    window.speechSynthesis.speak(utt)
  })
}

export function stopActiveSpeech() {
  if (_activeSynth) { try { _activeSynth.close() } catch {} _activeSynth = null }
  window.speechSynthesis?.cancel()
}

// ── Main hook ────────────────────────────────────────────────
export default function useVoiceLive({ onMessage, onTranscript, onStateChange } = {}) {
  const [voiceState, setVoiceState] = useState('idle') // idle | listening | thinking | speaking
  const [transcript, setTranscript] = useState('')     // current user speech (interim)
  const [lumenText, setLumenText] = useState('')        // current Lumen response text

  const activeRef = useRef(false)
  const recognizerRef = useRef(null)
  const abortControllerRef = useRef(null)  // cancels ongoing LLM + TTS
  const tokenDataRef = useRef(null)

  const updateState = useCallback((s) => {
    setVoiceState(s)
    onStateChange?.(s)
  }, [onStateChange])

  // ── Stop everything ───────────────────────────────────────
  const deactivate = useCallback(() => {
    activeRef.current = false
    abortControllerRef.current?.abort()
    abortControllerRef.current = null
    stopActiveSpeech()

    if (recognizerRef.current) {
      try {
        recognizerRef.current.stopContinuousRecognitionAsync(
          () => { try { recognizerRef.current?.close() } catch {} recognizerRef.current = null },
          () => { recognizerRef.current = null }
        )
      } catch { recognizerRef.current = null }
    }
    updateState('idle')
    setTranscript('')
    setLumenText('')
  }, [updateState])

  // ── Stream LLM + TTS pipeline ─────────────────────────────
  const processUtterance = useCallback(async (text) => {
    if (!text.trim() || !activeRef.current) return

    // Cancel any ongoing response
    abortControllerRef.current?.abort()
    const ctrl = new AbortController()
    abortControllerRef.current = ctrl

    stopActiveSpeech()
    updateState('thinking')
    setLumenText('')

    // Show user message in chat
    onMessage?.({ role: 'user', content: text, id: Date.now() + '-u' })

    try {
      const stored = localStorage.getItem('lumen.token') || localStorage.getItem('lumen_token')
      const resp = await fetch('/ag-ui/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${stored}` },
        body: JSON.stringify({ message: text, stream: true }),
        signal: ctrl.signal,
      })

      if (!resp.ok || ctrl.signal.aborted) return

      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let fullText = ''
      let pendingText = ''
      let isFirstChunk = true

      const flushSentence = async (sentence, isLast = false) => {
        if (ctrl.signal.aborted || !activeRef.current) return
        if (isFirstChunk) { updateState('speaking'); isFirstChunk = false }
        await speakSentence(sentence, tokenDataRef.current, { signal: ctrl.signal })
        if (isLast && !ctrl.signal.aborted && activeRef.current) {
          // After final sentence, restart listening
          onMessage?.({ role: 'lumen', content: fullText, id: Date.now() + '-l' })
          setLumenText('')
          startListening()
        }
      }

      let sentenceQueue = Promise.resolve()

      while (true) {
        if (ctrl.signal.aborted) break
        const { done, value } = await reader.read()
        if (done) break

        const chunk = decoder.decode(value, { stream: true })
        // Parse SSE lines
        for (const line of chunk.split('\n')) {
          if (!line.startsWith('data:')) continue
          const data = line.slice(5).trim()
          if (!data || data === '[DONE]') continue
          try {
            const parsed = JSON.parse(data)
            const token = parsed.delta || parsed.text || parsed.content || ''
            if (!token) continue

            fullText += token
            pendingText += token
            setLumenText(fullText)

            // Check if we have a complete sentence to speak
            const sentences = splitSentences(pendingText)
            if (sentences.length > 1) {
              // All but last are complete
              for (let i = 0; i < sentences.length - 1; i++) {
                const s = sentences[i]
                sentenceQueue = sentenceQueue.then(() => flushSentence(s))
              }
              pendingText = sentences[sentences.length - 1]
            }
          } catch {}
        }
      }

      // Speak remaining text
      if (pendingText.trim() && !ctrl.signal.aborted) {
        sentenceQueue = sentenceQueue.then(() => flushSentence(pendingText.trim(), true))
      } else if (!ctrl.signal.aborted && activeRef.current) {
        await sentenceQueue
        if (fullText) onMessage?.({ role: 'lumen', content: fullText, id: Date.now() + '-l' })
        setLumenText('')
        startListening()
      }

    } catch (err) {
      if (err.name !== 'AbortError') {
        console.error('Voice LLM error:', err)
        if (activeRef.current) startListening()
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onMessage, updateState])

  // ── Start continuous Azure Speech recognition ─────────────
  const startListening = useCallback(async () => {
    if (!activeRef.current) return
    // Clean up any previous recognizer
    if (recognizerRef.current) {
      try { recognizerRef.current.close() } catch {}
      recognizerRef.current = null
    }

    updateState('listening')
    setTranscript('')

    const tokenData = await fetchSpeechToken()
    tokenDataRef.current = tokenData

    if (tokenData?.available && tokenData?.token) {
      try {
        const sdk = await import('microsoft-cognitiveservices-speech-sdk')
        if (!activeRef.current) return

        const speechConfig = sdk.SpeechConfig.fromAuthorizationToken(tokenData.token, tokenData.region || 'eastus')
        speechConfig.speechRecognitionLanguage = 'en-US'

        // Use semantic segmentation for natural turn detection
        speechConfig.setProperty(sdk.PropertyId.Speech_SegmentationSilenceTimeoutMs, '1200')

        const audioConfig = sdk.AudioConfig.fromDefaultMicrophoneInput()
        const recognizer = new sdk.SpeechRecognizer(speechConfig, audioConfig)
        recognizerRef.current = recognizer

        recognizer.recognizing = (_, e) => {
          if (e.result.reason === sdk.ResultReason.RecognizingSpeech) {
            setTranscript(e.result.text)
          }
        }

        recognizer.recognized = (_, e) => {
          if (e.result.reason === sdk.ResultReason.RecognizedSpeech && e.result.text?.trim()) {
            const text = e.result.text.trim()
            setTranscript('')
            onTranscript?.(text)

            // Stop recognition while processing
            try {
              recognizer.stopContinuousRecognitionAsync(
                () => { recognizerRef.current = null },
                () => { recognizerRef.current = null }
              )
            } catch { recognizerRef.current = null }

            const lower = text.toLowerCase()
            if (['stop', 'exit', 'goodbye', 'bye lumen', 'stop voice'].some(w => lower.includes(w))) {
              deactivate()
              return
            }
            processUtterance(text)
          }
        }

        recognizer.canceled = () => {
          recognizerRef.current = null
          if (activeRef.current) setTimeout(startListening, 1000)
        }

        recognizer.startContinuousRecognitionAsync(
          () => {},
          (err) => {
            console.warn('Azure Speech start failed:', err)
            recognizerRef.current = null
            // Fallback to Web Speech
            if (activeRef.current) startWebSpeechLoop()
          }
        )
        return
      } catch (err) {
        console.warn('Azure Speech SDK unavailable:', err)
      }
    }

    // Fallback: Web Speech API
    startWebSpeechLoop()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [updateState, processUtterance, deactivate, onTranscript])

  // ── Web Speech fallback loop ──────────────────────────────
  const startWebSpeechLoop = useCallback(() => {
    if (!activeRef.current) return
    const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SpeechRec) { updateState('idle'); return }

    const rec = new SpeechRec()
    rec.lang = 'en-US'
    rec.interimResults = true
    rec.continuous = false
    rec.maxAlternatives = 1

    rec.onstart = () => updateState('listening')
    rec.onresult = (e) => {
      const last = e.results[e.results.length - 1]
      const text = last[0].transcript.trim()
      if (last.isFinal && text) {
        setTranscript('')
        onTranscript?.(text)
        const lower = text.toLowerCase()
        if (['stop', 'exit', 'goodbye', 'bye lumen'].some(w => lower.includes(w))) {
          deactivate(); return
        }
        processUtterance(text)
      } else {
        setTranscript(text)
      }
    }
    rec.onerror = (e) => {
      if (e.error !== 'not-allowed' && activeRef.current) setTimeout(startWebSpeechLoop, 600)
    }
    rec.onend = () => {
      // Will be restarted by processUtterance → startListening after TTS
    }
    try { rec.start() } catch { if (activeRef.current) setTimeout(startWebSpeechLoop, 600) }
  }, [updateState, processUtterance, deactivate, onTranscript])

  // ── Activate voice live ───────────────────────────────────
  const activate = useCallback(() => {
    if (activeRef.current) { deactivate(); return }
    activeRef.current = true
    startListening()
  }, [startListening, deactivate])

  // ── Barge-in: user taps while Lumen is speaking ───────────
  const bargeIn = useCallback(() => {
    if (voiceState !== 'speaking') return
    abortControllerRef.current?.abort()
    abortControllerRef.current = null
    stopActiveSpeech()
    setLumenText('')
    startListening()
  }, [voiceState, startListening])

  useEffect(() => () => {
    activeRef.current = false
    abortControllerRef.current?.abort()
    stopActiveSpeech()
    if (recognizerRef.current) {
      try { recognizerRef.current.close() } catch {}
      recognizerRef.current = null
    }
  }, [])

  return {
    voiceState,   // 'idle' | 'listening' | 'thinking' | 'speaking'
    transcript,   // live interim speech text
    lumenText,    // streaming Lumen response text
    activate,     // toggle voice live on/off
    deactivate,
    bargeIn,      // interrupt Lumen mid-speech
    isActive: voiceState !== 'idle',
  }
}
