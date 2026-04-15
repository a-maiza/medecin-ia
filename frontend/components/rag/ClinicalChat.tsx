"use client"

import { useEffect, useRef, useState } from "react"
import { BookOpen, Loader2, Send } from "lucide-react"
import { api } from "@/lib/api"
import type { RAGQueryResponse, RAGSource } from "@/lib/types"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { cn } from "@/lib/utils"

interface Message {
  id: string
  role: "user" | "assistant"
  content: string
  sources?: RAGSource[]
}

interface ClinicalChatProps {
  patientId?: string
  placeholder?: string
}

const NAMESPACE_LABELS: Record<string, string> = {
  ccam:           "CCAM",
  has:            "HAS",
  vidal:          "VIDAL",
  patient_history:"Historique patient",
  doctor_corpus:  "Corpus médecin",
}

export function ClinicalChat({ patientId, placeholder }: ClinicalChatProps) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState("")
  const [isLoading, setIsLoading] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages])

  async function handleSend(e: React.FormEvent) {
    e.preventDefault()
    const question = input.trim()
    if (!question || isLoading) return

    const userMsg: Message = {
      id: crypto.randomUUID(),
      role: "user",
      content: question,
    }
    setMessages((prev) => [...prev, userMsg])
    setInput("")
    setIsLoading(true)

    try {
      const { data } = await api.post<RAGQueryResponse>("/rag/query", {
        question,
        patient_id: patientId,
      })
      const assistantMsg: Message = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: data.answer,
        sources: data.sources,
      }
      setMessages((prev) => [...prev, assistantMsg])
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: "Une erreur est survenue. Veuillez réessayer.",
        },
      ])
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 && (
          <div className="text-center text-muted-foreground mt-12 space-y-2">
            <BookOpen className="h-10 w-10 mx-auto opacity-40" />
            <p className="text-sm">Posez une question clinique pour interroger la base de connaissances.</p>
            <div className="flex flex-wrap gap-2 justify-center mt-4">
              {QUICK_QUESTIONS.map((q) => (
                <button
                  key={q}
                  onClick={() => setInput(q)}
                  className="text-xs px-3 py-1 rounded-full border border-primary/30 text-primary/80 hover:bg-primary/10 transition-colors"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <div key={msg.id} className={cn("flex", msg.role === "user" ? "justify-end" : "justify-start")}>
            <div className={cn("max-w-[80%]", msg.role === "user" ? "items-end" : "items-start")}>
              <div
                className={cn(
                  "rounded-lg px-4 py-3 text-sm",
                  msg.role === "user"
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted text-foreground"
                )}
              >
                <p className="whitespace-pre-wrap">{msg.content}</p>
              </div>

              {/* Sources */}
              {msg.sources && msg.sources.length > 0 && (
                <Card className="mt-2">
                  <CardContent className="p-3 space-y-1">
                    <p className="text-xs font-medium text-muted-foreground mb-2">Sources</p>
                    {msg.sources.map((src, i) => (
                      <SourceChip key={i} source={src} />
                    ))}
                  </CardContent>
                </Card>
              )}
            </div>
          </div>
        ))}

        {isLoading && (
          <div className="flex justify-start">
            <div className="bg-muted rounded-lg px-4 py-3">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      <Separator />

      {/* Input */}
      <form onSubmit={handleSend} className="p-4 flex gap-2">
        <Input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={placeholder ?? "Posez une question clinique…"}
          disabled={isLoading}
          className="flex-1"
        />
        <Button type="submit" size="icon" disabled={!input.trim() || isLoading}>
          <Send className="h-4 w-4" />
        </Button>
      </form>
    </div>
  )
}

function SourceChip({ source }: { source: RAGSource }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <Badge variant="secondary" className="text-[10px] shrink-0">
        {NAMESPACE_LABELS[source.namespace] ?? source.namespace}
      </Badge>
      <span className="text-muted-foreground truncate">
        {source.document_title}
        {source.section ? ` › ${source.section}` : ""}
      </span>
    </div>
  )
}

const QUICK_QUESTIONS = [
  "Quelles sont les contre-indications de la metformine ?",
  "Quelle est la posologie de l'amoxicilline pour une angine ?",
  "Critères HAS pour le dépistage du cancer colorectal",
]
