"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { FileText, Loader2, MessageSquare, Trash2, Upload } from "lucide-react"
import { api } from "@/lib/api"
import type { Document } from "@/lib/types"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Separator } from "@/components/ui/separator"
import { ClinicalChat } from "@/components/rag/ClinicalChat"

type ViewMode = "docs" | "chat"

const STATUS_LABELS: Record<string, { label: string; variant: "secondary" | "warning" | "success" | "destructive" }> = {
  pending:  { label: "En attente",  variant: "secondary" },
  indexing: { label: "Indexation…", variant: "warning"   },
  indexed:  { label: "Indexé",      variant: "success"   },
  error:    { label: "Erreur",      variant: "destructive"},
}

export default function KnowledgeBasePage() {
  const [documents, setDocuments] = useState<Document[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [isUploading, setIsUploading] = useState(false)
  const [view, setView] = useState<ViewMode>("docs")
  const [indexingIds, setIndexingIds] = useState<Set<string>>(new Set())
  const fileInputRef = useRef<HTMLInputElement>(null)
  const pollTimers = useRef<Map<string, ReturnType<typeof setInterval>>>(new Map())

  const loadDocuments = useCallback(async () => {
    setIsLoading(true)
    try {
      const { data } = await api.get<Document[]>("/documents")
      setDocuments(data)
      // Start polling for any currently indexing docs
      data.filter((d) => d.status === "indexing" || d.status === "pending").forEach((d) => {
        startPolling(d.id)
      })
    } finally {
      setIsLoading(false)
    }
  }, []) // eslint-disable-line

  useEffect(() => {
    loadDocuments()
    return () => {
      pollTimers.current.forEach((t) => clearInterval(t))
    }
  }, [loadDocuments])

  function startPolling(docId: string) {
    if (pollTimers.current.has(docId)) return
    setIndexingIds((prev) => new Set([...prev, docId]))
    const timer = setInterval(async () => {
      try {
        const { data } = await api.get<{ status: string; progress?: number }>(`/documents/${docId}/status`)
        if (data.status === "indexed" || data.status === "error") {
          clearInterval(pollTimers.current.get(docId))
          pollTimers.current.delete(docId)
          setIndexingIds((prev) => { const n = new Set(prev); n.delete(docId); return n })
          setDocuments((prev) =>
            prev.map((d) => (d.id === docId ? { ...d, status: data.status as Document["status"] } : d))
          )
        }
      } catch {
        clearInterval(pollTimers.current.get(docId))
        pollTimers.current.delete(docId)
      }
    }, 2000)
    pollTimers.current.set(docId, timer)
  }

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    if (!["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"].includes(file.type)) {
      alert("Seuls les fichiers PDF et DOCX sont acceptés.")
      return
    }
    if (file.size > 50 * 1024 * 1024) {
      alert("Fichier trop volumineux (max 50 Mo).")
      return
    }
    setIsUploading(true)
    try {
      const formData = new FormData()
      formData.append("file", file)
      const { data } = await api.post<{ id: string }>("/documents/upload", formData, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      await loadDocuments()
      startPolling(data.id)
    } catch {
      alert("Erreur lors de l'upload.")
    } finally {
      setIsUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ""
    }
  }

  async function handleDelete(docId: string) {
    if (!confirm("Supprimer ce document ?")) return
    await api.delete(`/documents/${docId}`)
    setDocuments((prev) => prev.filter((d) => d.id !== docId))
  }

  return (
    <div className="p-6 h-full flex flex-col">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Base de connaissances</h1>
        <div className="flex gap-2">
          <Button
            variant={view === "docs" ? "default" : "outline"}
            size="sm"
            onClick={() => setView("docs")}
          >
            <FileText className="h-4 w-4 mr-2" />
            Documents
          </Button>
          <Button
            variant={view === "chat" ? "default" : "outline"}
            size="sm"
            onClick={() => setView("chat")}
          >
            <MessageSquare className="h-4 w-4 mr-2" />
            Chat clinique
          </Button>
        </div>
      </div>

      {view === "chat" ? (
        <Card className="flex-1 flex flex-col overflow-hidden">
          <ClinicalChat placeholder="Ex : Quelles sont les recommandations HAS pour l'HTA ?" />
        </Card>
      ) : (
        <>
          {/* Upload */}
          <div className="mb-6">
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.docx"
              className="hidden"
              onChange={handleUpload}
            />
            <Button onClick={() => fileInputRef.current?.click()} disabled={isUploading}>
              {isUploading ? (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              ) : (
                <Upload className="h-4 w-4 mr-2" />
              )}
              {isUploading ? "Envoi en cours…" : "Importer un document (PDF/DOCX, 50 Mo max)"}
            </Button>
          </div>

          {/* Document list */}
          {isLoading ? (
            <p className="text-muted-foreground">Chargement…</p>
          ) : documents.length === 0 ? (
            <Card>
              <CardContent className="py-12 text-center text-muted-foreground text-sm">
                Aucun document importé. Ajoutez des protocoles, guides HAS ou ordonnances types.
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-3">
              {documents.map((doc) => {
                const { label, variant } = STATUS_LABELS[doc.status] ?? { label: doc.status, variant: "secondary" as const }
                const isIndexing = indexingIds.has(doc.id)
                return (
                  <Card key={doc.id}>
                    <CardContent className="py-3 px-4">
                      <div className="flex items-center gap-3">
                        <FileText className="h-5 w-5 text-muted-foreground shrink-0" />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium truncate">{doc.filename}</span>
                            <Badge variant={variant}>{label}</Badge>
                          </div>
                          {isIndexing && (
                            <Progress value={undefined} className="h-1 mt-1.5 w-48" />
                          )}
                          {doc.chunk_count != null && (
                            <p className="text-xs text-muted-foreground mt-0.5">
                              {doc.chunk_count} fragments indexés
                            </p>
                          )}
                        </div>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="shrink-0 text-muted-foreground hover:text-destructive"
                          onClick={() => handleDelete(doc.id)}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </CardContent>
                  </Card>
                )
              })}
            </div>
          )}
        </>
      )}
    </div>
  )
}
