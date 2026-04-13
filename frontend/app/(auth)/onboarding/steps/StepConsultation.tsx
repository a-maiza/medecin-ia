"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"

interface StepConsultationProps {
  patientId: string
}

export default function StepConsultation({ patientId }: StepConsultationProps) {
  const router = useRouter()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleStartDemo = async () => {
    setLoading(true)
    setError(null)
    try {
      const { data } = await api.post<{ id: string }>("/consultations", {
        patient_id: patientId,
        motif: "Consultation de démonstration — onboarding",
        date: new Date().toISOString(),
      })
      router.push(`/dashboard/consultation/${data.id}`)
    } catch {
      setError("Impossible de créer la consultation de démonstration.")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold">Lancer votre première consultation démo</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Découvrez la transcription en temps réel, la génération SOAP et les alertes
          cliniques sur une consultation de démonstration.
        </p>
      </div>

      <ul className="space-y-2 text-sm text-muted-foreground list-disc list-inside">
        <li>Transcription audio via Whisper large-v3</li>
        <li>Génération SOAP en streaming avec Claude Sonnet</li>
        <li>Détection automatique des interactions médicamenteuses</li>
        <li>Édition inline et validation du compte-rendu</li>
      </ul>

      {error && (
        <p className="text-sm text-destructive bg-destructive/10 px-3 py-2 rounded-md">{error}</p>
      )}

      <Button onClick={handleStartDemo} className="w-full" size="lg" disabled={loading}>
        {loading ? "Création de la consultation…" : "Démarrer la consultation démo"}
      </Button>

      <Button
        variant="ghost"
        className="w-full"
        onClick={() => router.push("/dashboard")}
      >
        Passer — accéder au tableau de bord
      </Button>
    </div>
  )
}
