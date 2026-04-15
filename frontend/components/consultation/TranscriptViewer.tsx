"use client"

import { useEffect, useRef } from "react"
import { Mic, MicOff } from "lucide-react"
import { cn } from "@/lib/utils"
import type { TranscriptWord } from "@/lib/types"

interface TranscriptViewerProps {
  words: TranscriptWord[]
  isRecording: boolean
}

/**
 * Displays the live transcription stream with per-word confidence colouring:
 *   probability ≥ 0.70 → normal text
 *   probability < 0.70  → orange (uncertain)
 *   probability < 0.50  → red (low confidence)
 */
export function TranscriptViewer({ words, isRecording }: TranscriptViewerProps) {
  const bottomRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom as new words arrive
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [words])

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b bg-white">
        <span className="text-sm font-medium text-muted-foreground">Transcription</span>
        <div className="flex items-center gap-2">
          {isRecording ? (
            <>
              <span className="h-2 w-2 rounded-full bg-red-500 animate-pulse" />
              <Mic className="h-4 w-4 text-red-500" />
              <span className="text-xs text-red-500 font-medium">Enregistrement</span>
            </>
          ) : (
            <MicOff className="h-4 w-4 text-muted-foreground" />
          )}
        </div>
      </div>

      {/* Transcript body */}
      <div className="flex-1 overflow-y-auto p-4 font-mono text-sm leading-7 bg-slate-50">
        {words.length === 0 ? (
          <p className="text-muted-foreground text-center mt-8">
            {isRecording
              ? "En attente de parole…"
              : "Démarrez l'enregistrement pour voir la transcription."}
          </p>
        ) : (
          <p>
            {words.map((word, i) => (
              <Word key={i} word={word} />
            ))}
          </p>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Confidence legend */}
      <div className="px-4 py-2 border-t bg-white flex items-center gap-4 text-xs text-muted-foreground">
        <span className="font-medium">Confiance :</span>
        <span className="text-foreground">≥ 70 % normal</span>
        <span className="text-amber-600">50–70 % incertain</span>
        <span className="text-red-600">&lt; 50 % faible</span>
      </div>
    </div>
  )
}

function Word({ word }: { word: TranscriptWord }) {
  const colorClass =
    word.probability >= 0.7
      ? "text-foreground"
      : word.probability >= 0.5
      ? "text-amber-600"
      : "text-red-600"

  return (
    <span
      className={cn("mr-1", colorClass)}
      title={`Confiance : ${Math.round(word.probability * 100)}%`}
    >
      {word.word}
    </span>
  )
}
