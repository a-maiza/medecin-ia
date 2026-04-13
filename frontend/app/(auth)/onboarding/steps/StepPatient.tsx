"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { api } from "@/lib/api"

interface StepPatientProps {
  onNext: (patientId: string) => void
}

export default function StepPatient({ onNext }: StepPatientProps) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [form, setForm] = useState({
    nom_pseudonyme: "PATIENT TEST",
    date_naissance: "1980-01-01",
    sexe: "M",
  })

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    setForm((prev) => ({ ...prev, [e.target.name]: e.target.value }))
  }

  const handleCreate = async () => {
    setLoading(true)
    setError(null)
    try {
      const { data } = await api.post<{ id: string }>("/patients", form)
      onNext(data.id)
    } catch {
      setError("Impossible de créer le patient de test. Veuillez réessayer.")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold">Créer un patient de test</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Ce patient de démonstration vous permettra de tester toutes les
          fonctionnalités de MédecinAI en toute sécurité.
        </p>
      </div>

      <div className="space-y-3">
        <div className="space-y-1">
          <Label htmlFor="nom_pseudonyme">Pseudonyme</Label>
          <Input
            id="nom_pseudonyme"
            name="nom_pseudonyme"
            value={form.nom_pseudonyme}
            onChange={handleChange}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="date_naissance">Date de naissance</Label>
          <Input
            id="date_naissance"
            name="date_naissance"
            type="date"
            value={form.date_naissance}
            onChange={handleChange}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="sexe">Sexe</Label>
          <select
            id="sexe"
            name="sexe"
            value={form.sexe}
            onChange={handleChange}
            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <option value="M">Homme</option>
            <option value="F">Femme</option>
            <option value="autre">Autre</option>
          </select>
        </div>
      </div>

      {error && (
        <p className="text-sm text-destructive bg-destructive/10 px-3 py-2 rounded-md">{error}</p>
      )}

      <Button onClick={handleCreate} className="w-full" disabled={loading}>
        {loading ? "Création…" : "Créer le patient de test"}
      </Button>
    </div>
  )
}
