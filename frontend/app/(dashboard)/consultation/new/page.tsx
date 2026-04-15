"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { Loader2, Mic, MicOff, Sparkles } from "lucide-react"
import { api } from "@/lib/api"
import type { Alert, Patient, SOAPNote } from "@/lib/types"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { TranscriptViewer } from "@/components/consultation/TranscriptViewer"
import { SOAPEditor } from "@/components/consultation/SOAPEditor"
import { useTranscription } from "@/hooks/useTranscription"
import type { TranscriptWord } from "@/lib/types"

type Step = "setup" | "recording" | "soap"

export default function NewConsultationPage() {
  const router = useRouter()

  // ── Step state ────────────────────────────────────────────────────────────
  const [step, setStep] = useState<Step>("setup")

  // ── Patient selection ─────────────────────────────────────────────────────
  const [patients, setPatients] = useState<Patient[]>([])
  const [selectedPatientId, setSelectedPatientId] = useState("")
  const [motif, setMotif] = useState("")
  const [searchQuery, setSearchQuery] = useState("")

  // ── Consultation ──────────────────────────────────────────────────────────
  const [consultationId, setConsultationId] = useState("")
  const [sessionId] = useState(() => crypto.randomUUID())

  // ── SOAP ──────────────────────────────────────────────────────────────────
  const [soap, setSoap] = useState<SOAPNote | null>(null)
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [isGenerating, setIsGenerating] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [isValidating, setIsValidating] = useState(false)
  const [autoSaveLabel, setAutoSaveLabel] = useState("")

  // ── Load patients ─────────────────────────────────────────────────────────
  useEffect(() => {
    api.get<Patient[]>("/patients?limit=100").then((r) => setPatients(r.data)).catch(() => {})
  }, [])

  const filteredPatients = patients.filter((p) =>
    p.nom.toLowerCase().includes(searchQuery.toLowerCase()) ||
    (p.ins ?? "").includes(searchQuery)
  )

  // ── Transcription hook ───────────────────────────────────────────────────
  const handleAutoSave = useCallback(async () => {
    if (!consultationId) return
    setAutoSaveLabel("Sauvegarde…")
    setTimeout(() => setAutoSaveLabel("Sauvegardé ✓"), 1200)
  }, [consultationId])

  const { words, fullText, state: recordingState, start, stop } = useTranscription({
    sessionId,
    consultationId,
    onAutoSave: handleAutoSave,
  })

  // ── Step 1 → 2: Start consultation ───────────────────────────────────────
  async function handleStartRecording() {
    if (!selectedPatientId || !motif.trim()) return
    try {
      const { data } = await api.post("/consultations", {
        patient_id: selectedPatientId,
        motif: motif.trim(),
      })
      setConsultationId(data.id)
      setAlerts(Object.values(data.alerts ?? {}))
      setStep("recording")
      await start()
    } catch {
      alert("Erreur lors de la création de la consultation.")
    }
  }

  // ── Step 2 → 3: Stop recording + generate SOAP ───────────────────────────
  async function handleGenerateSOAP() {
    stop()
    setIsGenerating(true)
    try {
      const { data } = await api.post(`/soap/generate`, {
        consultation_id: consultationId,
      })
      setSoap(data.soap_generated ?? data)
      setAlerts(data.alerts ? Object.values(data.alerts) : [])
      setStep("soap")
    } catch {
      alert("Erreur lors de la génération du SOAP.")
    } finally {
      setIsGenerating(false)
    }
  }

  // ── SOAP save ─────────────────────────────────────────────────────────────
  async function handleSaveSOAP() {
    if (!soap) return
    setIsSaving(true)
    try {
      await api.patch(`/soap/${consultationId}`, soap)
    } finally {
      setIsSaving(false)
    }
  }

  // ── SOAP validate ─────────────────────────────────────────────────────────
  async function handleValidate(justification?: string) {
    setIsValidating(true)
    try {
      await api.post(`/soap/${consultationId}/validate`, { justification })
      router.push(`/consultation/${consultationId}`)
    } finally {
      setIsValidating(false)
    }
  }

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="p-6 max-w-7xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold">Nouvelle consultation</h1>
        <StepIndicator step={step} />
      </div>

      {/* ── Setup step ──────────────────────────────────────────────────────── */}
      {step === "setup" && (
        <Card className="max-w-lg">
          <CardHeader>
            <CardTitle>Sélection du patient et motif</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <Label htmlFor="search">Recherche patient</Label>
              <Input
                id="search"
                placeholder="Nom ou numéro INS…"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="mt-1"
              />
            </div>
            <div>
              <Label htmlFor="patient">Patient</Label>
              <Select value={selectedPatientId} onValueChange={setSelectedPatientId}>
                <SelectTrigger id="patient" className="mt-1">
                  <SelectValue placeholder="Choisir un patient…" />
                </SelectTrigger>
                <SelectContent>
                  {filteredPatients.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.nom} {p.ins ? `— INS ${p.ins}` : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label htmlFor="motif">Motif de consultation</Label>
              <Input
                id="motif"
                placeholder="Ex : douleur thoracique, renouvellement ordonnance…"
                value={motif}
                onChange={(e) => setMotif(e.target.value)}
                className="mt-1"
              />
            </div>
            <Button
              className="w-full"
              onClick={handleStartRecording}
              disabled={!selectedPatientId || !motif.trim()}
            >
              <Mic className="h-4 w-4 mr-2" />
              Démarrer l&apos;enregistrement
            </Button>
          </CardContent>
        </Card>
      )}

      {/* ── Recording step ──────────────────────────────────────────────────── */}
      {step === "recording" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 h-[calc(100vh-14rem)]">
          <Card className="flex flex-col overflow-hidden">
            <TranscriptViewer words={words} isRecording={recordingState === "recording"} />
          </Card>

          <Card className="flex flex-col">
            <CardHeader>
              <CardTitle className="text-base">Contrôle</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {autoSaveLabel && (
                <p className="text-xs text-muted-foreground">{autoSaveLabel}</p>
              )}
              <div className="flex gap-3">
                {recordingState === "recording" ? (
                  <Button variant="outline" onClick={stop}>
                    <MicOff className="h-4 w-4 mr-2" />
                    Mettre en pause
                  </Button>
                ) : (
                  <Button variant="outline" onClick={start}>
                    <Mic className="h-4 w-4 mr-2" />
                    Reprendre
                  </Button>
                )}
                <Button onClick={handleGenerateSOAP} disabled={isGenerating || !fullText.trim()}>
                  {isGenerating ? (
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  ) : (
                    <Sparkles className="h-4 w-4 mr-2" />
                  )}
                  Générer le SOAP
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* ── SOAP step ───────────────────────────────────────────────────────── */}
      {step === "soap" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="lg:col-span-1">
            <SOAPEditor
              soap={soap}
              alerts={alerts}
              isSaving={isSaving}
              isValidating={isValidating}
              onChange={setSoap}
              onSave={handleSaveSOAP}
              onValidate={handleValidate}
            />
          </div>
          <Card className="h-fit">
            <CardHeader>
              <CardTitle className="text-sm">Transcript</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-xs text-muted-foreground whitespace-pre-wrap font-mono leading-6">
                {fullText || "—"}
              </p>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  )
}

function StepIndicator({ step }: { step: Step }) {
  const steps: { id: Step; label: string }[] = [
    { id: "setup",     label: "Patient & motif" },
    { id: "recording", label: "Enregistrement" },
    { id: "soap",      label: "SOAP" },
  ]
  const currentIdx = steps.findIndex((s) => s.id === step)
  return (
    <div className="flex items-center gap-2 mt-2">
      {steps.map((s, i) => (
        <div key={s.id} className="flex items-center gap-2">
          <span
            className={`text-xs font-medium px-2 py-0.5 rounded-full ${
              i <= currentIdx
                ? "bg-primary text-primary-foreground"
                : "bg-muted text-muted-foreground"
            }`}
          >
            {s.label}
          </span>
          {i < steps.length - 1 && <span className="text-muted-foreground">→</span>}
        </div>
      ))}
    </div>
  )
}
