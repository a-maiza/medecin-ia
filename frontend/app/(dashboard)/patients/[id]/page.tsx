"use client"

import { useCallback, useEffect, useState } from "react"
import { useParams, useRouter } from "next/navigation"
import Link from "next/link"
import { ArrowLeft, Plus, Trash2 } from "lucide-react"
import { api } from "@/lib/api"
import type { ConsultationSummary, Patient } from "@/lib/types"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"

export default function PatientDetailPage() {
  const { id } = useParams<{ id: string }>()
  const router = useRouter()

  const [patient, setPatient] = useState<Patient | null>(null)
  const [consultations, setConsultations] = useState<ConsultationSummary[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)

  // Editable clinical fields
  const [dfg, setDfg] = useState("")
  const [grossesse, setGrossesse] = useState(false)
  const [newAllergie, setNewAllergie] = useState("")
  const [newTraitement, setNewTraitement] = useState("")
  const [newAntecedent, setNewAntecedent] = useState("")

  const load = useCallback(async () => {
    setIsLoading(true)
    try {
      const [pRes, cRes] = await Promise.all([
        api.get<Patient>(`/patients/${id}`),
        api.get<ConsultationSummary[]>(`/patients/${id}/consultations?limit=20`),
      ])
      const p = pRes.data
      setPatient(p)
      setDfg(p.dfg?.toString() ?? "")
      setGrossesse(p.grossesse)
      setConsultations(cRes.data)
    } catch {
      router.push("/patients")
    } finally {
      setIsLoading(false)
    }
  }, [id, router])

  useEffect(() => { load() }, [load])

  async function handleSave() {
    if (!patient) return
    setIsSaving(true)
    try {
      const { data } = await api.patch<Patient>(`/patients/${id}`, {
        dfg: dfg ? parseFloat(dfg) : null,
        grossesse,
        allergies: patient.allergies,
        traitements_actifs: patient.traitements_actifs,
        antecedents: patient.antecedents,
      })
      setPatient(data)
    } finally {
      setIsSaving(false)
    }
  }

  async function handleDelete() {
    if (!confirm("Supprimer définitivement ce dossier patient (RGPD) ? Cette action est irréversible.")) return
    setIsDeleting(true)
    try {
      await api.delete(`/patients/${id}`)
      router.push("/patients")
    } finally {
      setIsDeleting(false)
    }
  }

  function addItem(field: "allergies" | "traitements_actifs" | "antecedents", value: string) {
    if (!patient || !value.trim()) return
    setPatient({ ...patient, [field]: [...patient[field], value.trim()] })
  }

  function removeItem(field: "allergies" | "traitements_actifs" | "antecedents", idx: number) {
    if (!patient) return
    setPatient({ ...patient, [field]: patient[field].filter((_, i) => i !== idx) })
  }

  if (isLoading) return <div className="p-6 text-muted-foreground">Chargement…</div>
  if (!patient) return null

  return (
    <div className="p-6 max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-4 mb-6">
        <Button variant="ghost" size="icon" asChild>
          <Link href="/patients"><ArrowLeft className="h-4 w-4" /></Link>
        </Button>
        <div>
          <h1 className="text-2xl font-bold">{patient.nom}</h1>
          {patient.ins && <p className="text-sm text-muted-foreground">INS : {patient.ins}</p>}
        </div>
        <div className="ml-auto flex gap-2">
          {patient.grossesse && <Badge variant="warning">Grossesse</Badge>}
          {patient.dfg != null && patient.dfg < 30 && (
            <Badge variant="destructive">IRC sévère (DFG {patient.dfg})</Badge>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* ── Left: clinical data ──────────────────────────────────────────── */}
        <div className="lg:col-span-2 space-y-6">
          {/* DFG & Grossesse */}
          <Card>
            <CardHeader><CardTitle className="text-base">Données cliniques</CardTitle></CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <Label htmlFor="dfg">DFG (mL/min/1.73m²)</Label>
                  <Input
                    id="dfg"
                    type="number"
                    min="0"
                    max="200"
                    value={dfg}
                    onChange={(e) => setDfg(e.target.value)}
                    className="mt-1"
                  />
                </div>
                <div className="flex items-end pb-1">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={grossesse}
                      onChange={(e) => setGrossesse(e.target.checked)}
                    />
                    <span className="text-sm">Grossesse en cours</span>
                  </label>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Allergies */}
          <EditableList
            title="Allergies"
            items={patient.allergies}
            onRemove={(i) => removeItem("allergies", i)}
            newValue={newAllergie}
            onNewValueChange={setNewAllergie}
            onAdd={() => { addItem("allergies", newAllergie); setNewAllergie("") }}
            placeholder="Ajouter une allergie…"
            badgeVariant="destructive"
          />

          {/* Traitements actifs */}
          <EditableList
            title="Traitements actifs"
            items={patient.traitements_actifs}
            onRemove={(i) => removeItem("traitements_actifs", i)}
            newValue={newTraitement}
            onNewValueChange={setNewTraitement}
            onAdd={() => { addItem("traitements_actifs", newTraitement); setNewTraitement("") }}
            placeholder="Ajouter un traitement…"
            badgeVariant="secondary"
          />

          {/* Antécédents */}
          <EditableList
            title="Antécédents"
            items={patient.antecedents}
            onRemove={(i) => removeItem("antecedents", i)}
            newValue={newAntecedent}
            onNewValueChange={setNewAntecedent}
            onAdd={() => { addItem("antecedents", newAntecedent); setNewAntecedent("") }}
            placeholder="Ajouter un antécédent…"
            badgeVariant="outline"
          />

          <div className="flex gap-3">
            <Button onClick={handleSave} disabled={isSaving}>
              {isSaving ? "Sauvegarde…" : "Sauvegarder les modifications"}
            </Button>
            <Button variant="destructive" onClick={handleDelete} disabled={isDeleting}>
              <Trash2 className="h-4 w-4 mr-2" />
              {isDeleting ? "Suppression…" : "Supprimer le dossier (RGPD)"}
            </Button>
          </div>
        </div>

        {/* ── Right: consultation history ───────────────────────────────────── */}
        <div>
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle className="text-base">Consultations</CardTitle>
              <Button size="sm" asChild>
                <Link href={`/consultation/new?patient=${id}`}>
                  <Plus className="h-3.5 w-3.5 mr-1" />
                  Nouvelle
                </Link>
              </Button>
            </CardHeader>
            <CardContent>
              {consultations.length === 0 ? (
                <p className="text-sm text-muted-foreground">Aucune consultation.</p>
              ) : (
                <div className="space-y-3">
                  {consultations.map((c) => (
                    <div key={c.id}>
                      <Link href={`/consultation/${c.id}`} className="block hover:bg-accent rounded-md p-2 -mx-2 transition-colors">
                        <div className="flex items-center justify-between">
                          <span className="text-sm font-medium truncate">{c.motif}</span>
                          <StatusBadge status={c.status} />
                        </div>
                        <p className="text-xs text-muted-foreground mt-0.5">
                          {new Date(c.date).toLocaleDateString("fr-FR")}
                        </p>
                      </Link>
                      <Separator className="mt-2" />
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}

function EditableList({
  title,
  items,
  onRemove,
  newValue,
  onNewValueChange,
  onAdd,
  placeholder,
  badgeVariant,
}: {
  title: string
  items: string[]
  onRemove: (i: number) => void
  newValue: string
  onNewValueChange: (v: string) => void
  onAdd: () => void
  placeholder: string
  badgeVariant: "destructive" | "secondary" | "outline"
}) {
  return (
    <Card>
      <CardHeader className="pb-3"><CardTitle className="text-base">{title}</CardTitle></CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-2 min-h-[32px]">
          {items.length === 0 ? (
            <span className="text-sm text-muted-foreground">Aucun.</span>
          ) : (
            items.map((item, i) => (
              <Badge key={i} variant={badgeVariant} className="gap-1 cursor-pointer" onClick={() => onRemove(i)}>
                {item}
                <span className="text-xs opacity-60">×</span>
              </Badge>
            ))
          )}
        </div>
        <div className="flex gap-2">
          <Input
            value={newValue}
            onChange={(e) => onNewValueChange(e.target.value)}
            placeholder={placeholder}
            onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), onAdd())}
            className="h-8 text-sm"
          />
          <Button size="sm" variant="outline" onClick={onAdd} disabled={!newValue.trim()}>
            <Plus className="h-3.5 w-3.5" />
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

function StatusBadge({ status }: { status: ConsultationSummary["status"] }) {
  const map = {
    in_progress: { label: "En cours",  variant: "secondary" as const },
    generated:   { label: "Généré",    variant: "warning"   as const },
    validated:   { label: "Validé",    variant: "success"   as const },
    exported:    { label: "Exporté",   variant: "outline"   as const },
  }
  const { label, variant } = map[status] ?? { label: status, variant: "outline" as const }
  return <Badge variant={variant}>{label}</Badge>
}
