"use client"

import { useEffect, useState } from "react"
import { api } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

interface CabinetRow {
  id: string
  nom: string
  plan: string
  subscription_status: string
  trial_ends_at: string | null
  medecin_count: number
}

interface RAGMetrics {
  avg_quality_score: number
  p50_latency_ms: number
  p95_latency_ms: number
  chunks_by_namespace: Record<string, number>
}

export default function AdminDashboardPage() {
  const [cabinets, setCabinets] = useState<CabinetRow[]>([])
  const [metrics, setMetrics] = useState<RAGMetrics | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      api.get<CabinetRow[]>("/admin/cabinets").catch(() => ({ data: [] as CabinetRow[] })),
      api.get<RAGMetrics>("/admin/rag/metrics").catch(() => ({ data: null })),
    ]).then(([cabRes, metRes]) => {
      setCabinets(cabRes.data)
      setMetrics(metRes.data)
    }).finally(() => setIsLoading(false))
  }, [])

  const NAMESPACE_LABELS: Record<string, string> = {
    ccam:           "CCAM",
    has:            "HAS",
    vidal:          "VIDAL",
    patient_history:"Historique patient",
    doctor_corpus:  "Corpus médecin",
  }

  return (
    <div className="p-6 max-w-5xl">
      <h1 className="text-2xl font-bold mb-6">Tableau de bord admin</h1>

      {/* ── RAG Metrics ──────────────────────────────────────────────────────── */}
      {metrics && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
          <MetricCard label="Score qualité moyen" value={`${(metrics.avg_quality_score * 100).toFixed(0)} %`} />
          <MetricCard label="Latence p50" value={`${metrics.p50_latency_ms} ms`} />
          <MetricCard label="Latence p95" value={`${metrics.p95_latency_ms} ms`} />
          <MetricCard
            label="Total chunks"
            value={String(Object.values(metrics.chunks_by_namespace).reduce((a, b) => a + b, 0))}
          />
        </div>
      )}

      {/* ── Chunks by namespace ───────────────────────────────────────────────── */}
      {metrics && (
        <Card className="mb-8">
          <CardHeader><CardTitle className="text-base">Chunks par namespace</CardTitle></CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              {Object.entries(metrics.chunks_by_namespace).map(([ns, count]) => (
                <div key={ns} className="flex items-center justify-between p-3 rounded-lg bg-muted text-sm">
                  <span className="font-medium">{NAMESPACE_LABELS[ns] ?? ns}</span>
                  <span className="text-muted-foreground">{count.toLocaleString("fr-FR")}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* ── Cabinets list ────────────────────────────────────────────────────── */}
      <Card>
        <CardHeader><CardTitle className="text-base">Cabinets ({cabinets.length})</CardTitle></CardHeader>
        <CardContent>
          {isLoading ? (
            <p className="text-muted-foreground text-sm">Chargement…</p>
          ) : cabinets.length === 0 ? (
            <p className="text-muted-foreground text-sm">Aucun cabinet.</p>
          ) : (
            <div className="space-y-2">
              {cabinets.map((cab) => {
                const isExpired =
                  cab.subscription_status === "canceled" ||
                  (cab.trial_ends_at && new Date(cab.trial_ends_at) < new Date() && cab.subscription_status !== "active")
                return (
                  <div key={cab.id} className="flex items-center justify-between py-2 border-b last:border-0">
                    <div>
                      <p className="text-sm font-medium">{cab.nom}</p>
                      <p className="text-xs text-muted-foreground">
                        {cab.medecin_count} médecin{cab.medecin_count > 1 ? "s" : ""}
                        {cab.trial_ends_at && ` · essai jusqu'au ${new Date(cab.trial_ends_at).toLocaleDateString("fr-FR")}`}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge variant="outline">{cab.plan}</Badge>
                      <Badge variant={isExpired ? "destructive" : "success"}>
                        {cab.subscription_status}
                      </Badge>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <CardContent className="pt-4">
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className="text-2xl font-bold mt-1">{value}</p>
      </CardContent>
    </Card>
  )
}
