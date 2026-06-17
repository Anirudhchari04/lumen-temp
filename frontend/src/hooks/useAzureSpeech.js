/**
 * useAzureSpeech — simple interactive voice loop.
 * Click mic → speak → TTS response → mic auto-restarts.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

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
  } catch {
    return null
  }
}

const STOP_WORDS = ['stop', 'stop listening', 'goodbye', 'bye lumen', 'done', 'quit', 'exit']

export default function useAzureSpeech({ onResult, onError, onInterim } = {}) {
  const [listening, setListening] = useState(false)
  const [voiceLiveMode, setVoiceLiveMode] = useState(false)
  const [azureAvailable, setAzureAvailable] = useState(null)
  const recognizerRef = useRef(null)
  const fallbackRef = useRef(null)
  const activeRef = useRef(false)

  useEffect(() => {
    fetchSpeechToken().then(t => setAzureAvailable(!!(t?.available)))
  }, [])

  const stopAll = useCallback(() => {
    if (recognizerRef.current) {
      try {
        recognizerRef.current.stopContinuousRecognitionAsync(
          () => { try { recognizerRef.current?.close() } catch {} recognizerRef.current = null },
          () => { recognizerRef.current = null }
        )
      } catch { recognizerRef.current = null }
    }
    if (fallbackRef.current) {
      try { fallbackRef.current.stop() } catch {}
      fallbackRef.current = null
    }
    setListening(false)
  }, [])

  const stopVoiceLive = useCallback(() => {
    activeRef.current = false
    setVoiceLiveMode(false)
    stopAll()
    azureStopSpeaking()
  }, [stopAll])

  const _startOneListen = useCallback(async () => {
    if (!activeRef.current) return

    const tokenData = await fetchSpeechToken()

    if (tokenData?.available && tokenData?.token) {
      try {
        const sdk = await import('microsoft-cognitiveservices-speech-sdk')
        const speechConfig = sdk.SpeechConfig.fromAuthorizationToken(
          tokenData.token, tokenData.region || 'eastus'
        )
        speechConfig.speechRecognitionLanguage = 'en-US'

        const audioConfig = sdk.AudioConfig.fromDefaultMicrophoneInput()
        const recognizer = new sdk.SpeechRecognizer(speechConfig, audioConfig)
        recognizerRef.current = recognizer

        recognizer.recognizing = (_, e) => {
          if (e.result.reason === sdk.ResultReason.RecognizingSpeech) {
            onInterim?.(e.result.text)
          }
        }

        recognizer.recognized = (_, e) => {
          if (e.result.reason === sdk.ResultReason.RecognizedSpeech && e.result.text?.trim()) {
            const text = e.result.text.trim()
            try {
              recognizer.stopContinuousRecognitionAsync(
                () => { recognizerRef.current = null },
                () => { recognizerRef.current = null }
              )
            } catch { recognizerRef.current = null }
            setListening(false)
            const lower = text.toLowerCase()
            if (STOP_WORDS.some(w => lower.includes(w))) {
              stopVoiceLive()
              return
            }
            onResult?.(text)
          }
        }

        recognizer.canceled = () => {
          recognizerRef.current = null
          setListening(false)
          if (activeRef.current) setTimeout(_startOneListen, 1000)
        }

        recognizer.sessionStopped = () => {
          recognizerRef.current = null
          setListening(false)
        }

        recognizer.startContinuousRecognitionAsync(
          () => setListening(true),
          (err) => {
            recognizerRef.current = null
            onError?.(err)
            setListening(false)
            if (activeRef.current) setTimeout(_startOneListen, 1000)
          }
        )
        return
      } catch (err) {
        console.warn('Azure Speech SDK failed, using Web Speech:', err)
      }
    }

    // Web Speech fallback
    const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SpeechRec) { onError?.('Speech not supported'); return }

    const rec = new SpeechRec()
    rec.lang = 'en-US'
    rec.interimResults = true
    rec.continuous = false
    fallbackRef.current = rec

    rec.onstart = () => setListening(true)
    rec.onresult = (e) => {
      const last = e.results[e.results.length - 1]
      const text = last[0].transcript.trim()
      if (last.isFinal) {
        fallbackRef.current = null
        setListening(false)
        if (STOP_WORDS.some(w => text.toLowerCase().includes(w))) {
          stopVoiceLive()
        } else {
          onResult?.(text)
        }
      } else {
        onInterim?.(text)
      }
    }
    rec.onerror = (e) => {
      fallbackRef.current = null
      setListening(false)
      if (e.error !== 'not-allowed' && activeRef.current) setTimeout(_startOneListen, 800)
    }
    rec.onend = () => { fallbackRef.current = null; setListening(false) }

    try { rec.start() } catch { if (activeRef.current) setTimeout(_startOneListen, 800) }
  }, [onResult, onError, onInterim, stopVoiceLive])

  // Toggle voice live on/off
  const startListening = useCallback(async () => {
    if (activeRef.current) { stopVoiceLive(); return }
    activeRef.current = true
    setVoiceLiveMode(true)
    await _startOneListen()
  }, [_startOneListen, stopVoiceLive])

  // Called after TTS ends — restart mic automatically
  const onSpeakEnd = useCallback(() => {
    if (!activeRef.current) return
    if (recognizerRef.current) {
      try { recognizerRef.current.close() } catch {}
      recognizerRef.current = null
    }
    if (fallbackRef.current) {
      try { fallbackRef.current.stop() } catch {}
      fallbackRef.current = null
    }
    setListening(false)
    setTimeout(_startOneListen, 400)
  }, [_startOneListen])

  useEffect(() => () => { activeRef.current = false; stopAll() }, [stopAll])

  return { listening, voiceLiveMode, startListening, stopVoiceLive, onSpeakEnd, azureAvailable }
}

// ── Azure TTS ─────────────────────────────────────────────────

let _synthRef = null

export async function azureSpeak(text, { onEnd } = {}) {
  if (!text?.trim()) return
  const clean = text.replace(/[*#_`]/g, '').replace(/\n+/g, '. ').trim()

  const tokenData = await fetchSpeechToken()
  if (tokenData?.available && tokenData?.token) {
    try {
      const sdk = await import('microsoft-cognitiveservices-speech-sdk')
      const speechConfig = sdk.SpeechConfig.fromAuthorizationToken(
        tokenData.token, tokenData.region || 'eastus'
      )
      speechConfig.speechSynthesisVoiceName = 'en-IN-AaravNeural'
      const synthesizer = new sdk.SpeechSynthesizer(speechConfig)
      _synthRef = synthesizer
      synthesizer.speakTextAsync(
        clean,
        () => { synthesizer.close(); _synthRef = null; onEnd?.() },
        (err) => { console.warn('Azure TTS:', err); synthesizer.close(); _synthRef = null; _browserSpeak(clean, onEnd) }
      )
      return
    } catch {}
  }
  _browserSpeak(clean, onEnd)
}

export function azureStopSpeaking() {
  if (_synthRef) { try { _synthRef.close() } catch {} _synthRef = null }
  window.speechSynthesis?.cancel()
}

function _browserSpeak(text, onEnd) {
  if (!window.speechSynthesis) { onEnd?.(); return }
  window.speechSynthesis.cancel()
  const utt = new SpeechSynthesisUtterance(text)
  utt.rate = 1.0; utt.lang = 'en-US'
  if (onEnd) utt.onend = onEnd
  window.speechSynthesis.speak(utt)
}
