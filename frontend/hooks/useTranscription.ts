"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import type { TranscriptMessage, TranscriptWord } from "@/lib/types"

const WS_BASE = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000"

export type RecordingState = "idle" | "recording" | "stopping"

export interface UseTranscriptionOptions {
  sessionId: string
  consultationId: string
  onAutoSave?: () => void
}

export interface UseTranscriptionReturn {
  words: TranscriptWord[]
  fullText: string
  state: RecordingState
  start: () => Promise<void>
  stop: () => void
}

export function useTranscription({
  sessionId,
  consultationId,
  onAutoSave,
}: UseTranscriptionOptions): UseTranscriptionReturn {
  const [words, setWords] = useState<TranscriptWord[]>([])
  const [state, setState] = useState<RecordingState>("idle")

  const wsRef = useRef<WebSocket | null>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const autoSaveTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Auto-save every 30 seconds
  useEffect(() => {
    if (state === "recording") {
      autoSaveTimerRef.current = setInterval(() => {
        onAutoSave?.()
      }, 30_000)
    } else {
      if (autoSaveTimerRef.current) {
        clearInterval(autoSaveTimerRef.current)
        autoSaveTimerRef.current = null
      }
    }
    return () => {
      if (autoSaveTimerRef.current) clearInterval(autoSaveTimerRef.current)
    }
  }, [state, onAutoSave])

  const start = useCallback(async () => {
    if (state !== "idle") return

    // Request microphone
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      alert("Accès au microphone refusé. Veuillez autoriser l'accès dans les paramètres.")
      return
    }

    // Open WebSocket
    const token = await getAccessToken()
    const ws = new WebSocket(
      `${WS_BASE}/ws/transcription/${sessionId}?token=${token ?? ""}`
    )
    wsRef.current = ws

    ws.onmessage = (event) => {
      try {
        const msg: TranscriptMessage = JSON.parse(event.data)
        setWords((prev) => {
          if (msg.is_final) {
            return [...prev, ...msg.words]
          }
          // Replace tail with live partial words
          const finalCount = prev.filter((w) => w.probability >= 0).length
          return [...prev.slice(0, finalCount), ...msg.words]
        })
      } catch {
        // ignore parse errors
      }
    }

    ws.onerror = () => setState("idle")
    ws.onclose = () => setState("idle")

    // Start MediaRecorder — send PCM chunks over WebSocket
    const mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm" })
    mediaRecorderRef.current = mediaRecorder

    mediaRecorder.ondataavailable = async (e) => {
      if (ws.readyState === WebSocket.OPEN && e.data.size > 0) {
        const buffer = await e.data.arrayBuffer()
        const b64 = btoa(String.fromCharCode(...new Uint8Array(buffer)))
        ws.send(JSON.stringify({ audio_chunk: b64, consultation_id: consultationId }))
      }
    }

    mediaRecorder.start(250) // 250ms chunks
    setState("recording")
  }, [state, sessionId, consultationId])

  const stop = useCallback(() => {
    setState("stopping")
    mediaRecorderRef.current?.stop()
    mediaRecorderRef.current?.stream.getTracks().forEach((t) => t.stop())
    wsRef.current?.close()
    setState("idle")
  }, [])

  const fullText = words.map((w) => w.word).join(" ")

  return { words, fullText, state, start, stop }
}

async function getAccessToken(): Promise<string | null> {
  try {
    const res = await fetch("/api/auth/token", { cache: "no-store" })
    if (!res.ok) return null
    const data = await res.json()
    return data.token ?? null
  } catch {
    return null
  }
}
