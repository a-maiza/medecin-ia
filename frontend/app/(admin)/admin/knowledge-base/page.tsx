"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { FileText, Loader2, Trash2, Upload } from "lucide-react"
import { api } from "@/lib/api"
import type { Document } from "@/lib/types"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

const SOURCES = ["all", "ccam", "has", "vidal"] as const
type SourceFilter = typeof SOURCES[number]

const STATUS_LABELS: Record<string, { label: string; variant: "secondary" | "warning" | "success" | "destructive" }> = {
  pending:  { label: "En attente",  variant: "secondary" },
  indexing: { label: "Indexation…", variant: "warning"   },
  indexed:  { label: "Indexé",      variant: "success"   },
  error:    { label: "Erreur",      variant: "destructive"},
}

export default function AdminKnowledgeBasePage() {
  const [documents, setDocuments] = useState<Document[]>([])
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all")
  const [isLoading, setIsLoading] = useState(true)
  const [isUploading, setIsUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const load = useCallback(async () => {
    setIsLoading(true)
    try {
      const params = sourceFilter !== "all" ? `?source=${sourceFilter}` : ""
      const { data } = await api.get<Document[]>(`/documents${params}`)
      setDocuments(data)
    } finally {
      setIsLoading(false)
    }
  }, [sourceFilter])

  useEffect(() => { load() }, [load])

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setIsUploading(true)
    try {
      const formData = new FormData()
      formData.append("file", file)
      await api.post("/documents/upload", formData, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      await load()
    } catch {
      alert("Erreur lors de l'upload.")
    } finally {
      setIsUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ""
    }
  }

  async function handleDelete(docId: string) {
    if (!confirm("Supprimer ce document global ?")) return
    await api.delete(`/documents/${docId}`)
    setDocuments((prev) => prev.filter((d) => d.id !== docId))
  }

  // Metrics: chunks per source
  const chunksBySource = documents.reduce<Record<string, number>>((acc, d) => {
    acc[d.source] = (acc[d.source] ?? 0) + (d.chunk_count ?? 0)
    return acc
  }, {})

  return (
    <div className="p-6 max-w-4xl">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Base de connaissances globale</h1>
        <div className="flex gap-2">
          <input ref={fileInputRef} type="file" accept=".pdf,.docx" className="hidden" onChange={handleUpload} />
          <Button onClick={() => fileInputRef.current?.click()} disabled={isUploading}>
            {isUploading ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Upload className="h-4 w-4 mr-2" />}
            {isUploading ? "Envoi…" : "Importer"}
          </Button>
        </div>
      </div>

      {/* Metrics */}
      {Object.keys(chunksBySource).length > 0 && (
        <div className="flex flex-wrap gap-3 mb-6">
          {Object.entries(chunksBySource).map(([src, count]) => (
            <div key={src} className="px-3 py-1.5 rounded-lg border text-sm">
              <span className="font-medium uppercase text-xs text-muted-foreground">{src}</span>
              <span className="ml-2 font-bold">{count.toLocaleString("fr-FR")} chunks</span>
            </div>
          ))}
        </div>
      )}

      {/* Filter */}
      <div className="flex gap-3 mb-4">
        <Select value={sourceFilter} onValueChange={(v) => setSourceFilter(v as SourceFilter)}>
          <SelectTrigger className="w-40">
            <SelectValue placeholder="Source" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Toutes les sources</SelectItem>
            <SelectItem value="ccam">CCAM</SelectItem>
            <SelectItem value="has">HAS</SelectItem>
            <SelectItem value="vidal">VIDAL</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Document list */}
      {isLoading ? (
        <p className="text-muted-foreground">Chargement…</p>
      ) : documents.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">Aucun document.</CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader><CardTitle className="text-base">{documents.length} documents</CardTitle></CardHeader>
          <CardContent className="divide-y">
            {documents.map((doc) => {
              const { label, variant } = STATUS_LABELS[doc.status] ?? { label: doc.status, variant: "secondary" as const }
              return (
                <div key={doc.id} className="flex items-center gap-3 py-3">
                  <FileText className="h-4 w-4 text-muted-foreground shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate">{doc.filename}</p>
                    <div className="flex items-center gap-2 mt-0.5">
                      <Badge variant="outline" className="text-[10px]">{doc.source.toUpperCase()}</Badge>
                      <Badge variant={variant}>{label}</Badge>
                      {doc.chunk_count != null && (
                        <span className="text-xs text-muted-foreground">{doc.chunk_count} chunks</span>
                      )}
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="text-muted-foreground hover:text-destructive shrink-0"
                    onClick={() => handleDelete(doc.id)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              )
            })}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
