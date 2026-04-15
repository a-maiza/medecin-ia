"use client"

import { useState } from "react"
import { AlertTriangle, CheckCircle, Loader2, Save } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"
import type { Alert, SOAPNote } from "@/lib/types"

interface SOAPEditorProps {
  soap: SOAPNote | null
  alerts: Alert[]
  isSaving?: boolean
  isValidating?: boolean
  onChange: (updated: SOAPNote) => void
  onSave: () => void
  onValidate: (justification?: string) => void
}

const SOAP_SECTIONS: { key: keyof SOAPNote; label: string; description: string }[] = [
  { key: "subjective",  label: "S — Subjectif",   description: "Motif, plainte principale, antécédents pertinents" },
  { key: "objective",   label: "O — Objectif",     description: "Examen clinique, constantes, résultats" },
  { key: "assessment",  label: "A — Évaluation",   description: "Diagnostic, hypothèses diagnostiques" },
  { key: "plan",        label: "P — Plan",          description: "Traitement, ordonnance, suivi" },
]

export function SOAPEditor({
  soap,
  alerts,
  isSaving = false,
  isValidating = false,
  onChange,
  onSave,
  onValidate,
}: SOAPEditorProps) {
  const [justification, setJustification] = useState("")

  const hasCritique = alerts.some(
    (a) => a.severity === "CI_ABSOLUE" || a.severity === "CRITIQUE"
  )
  const hasRelative = alerts.some(
    (a) => a.severity === "CI_RELATIVE"
  )
  const canValidate = !hasCritique && (!hasRelative || justification.trim().length > 0)

  function handleSectionChange(key: keyof SOAPNote, value: string) {
    onChange({ ...(soap ?? {}), [key]: value })
  }

  return (
    <div className="flex flex-col gap-4">
      {/* ── Alerts ──────────────────────────────────────────────────────────── */}
      {alerts.length > 0 && (
        <div className="space-y-2">
          {alerts.map((alert, i) => (
            <AlertBadge key={i} alert={alert} />
          ))}
        </div>
      )}

      {/* ── CI_RELATIVE justification ────────────────────────────────────────── */}
      {hasRelative && !hasCritique && (
        <Card className="border-amber-200 bg-amber-50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-amber-800">
              Justification clinique obligatoire (CI_RELATIVE)
            </CardTitle>
          </CardHeader>
          <CardContent>
            <Textarea
              placeholder="Décrivez la justification clinique pour maintenir cette association médicamenteuse…"
              value={justification}
              onChange={(e) => setJustification(e.target.value)}
              className="min-h-[80px] border-amber-300 focus-visible:ring-amber-400"
            />
          </CardContent>
        </Card>
      )}

      {/* ── SOAP sections ───────────────────────────────────────────────────── */}
      {SOAP_SECTIONS.map(({ key, label, description }) => (
        <Card key={key as string}>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold text-primary">{label}</CardTitle>
            <p className="text-xs text-muted-foreground">{description}</p>
          </CardHeader>
          <CardContent>
            <Textarea
              value={typeof soap?.[key] === "string" ? (soap[key] as string) : ""}
              onChange={(e) => handleSectionChange(key, e.target.value)}
              placeholder={`${label}…`}
              className="min-h-[100px] text-sm"
            />
          </CardContent>
        </Card>
      ))}

      {/* ── CCAM / CIM-10 codes ─────────────────────────────────────────────── */}
      {(soap?.ccam_codes?.length || soap?.cim10_codes?.length) ? (
        <Card>
          <CardContent className="pt-4">
            <div className="flex flex-wrap gap-2">
              {soap?.ccam_codes?.map((code) => (
                <Badge key={code} variant="secondary">CCAM : {code}</Badge>
              ))}
              {soap?.cim10_codes?.map((code) => (
                <Badge key={code} variant="outline">CIM-10 : {code}</Badge>
              ))}
            </div>
          </CardContent>
        </Card>
      ) : null}

      {/* ── Actions ─────────────────────────────────────────────────────────── */}
      <div className="flex items-center gap-3 pt-2">
        <Button variant="outline" size="sm" onClick={onSave} disabled={isSaving}>
          {isSaving ? (
            <Loader2 className="h-4 w-4 mr-2 animate-spin" />
          ) : (
            <Save className="h-4 w-4 mr-2" />
          )}
          Sauvegarder
        </Button>

        <Button
          size="sm"
          onClick={() => onValidate(justification)}
          disabled={!canValidate || isValidating || !soap}
          className={cn(!canValidate && "opacity-50 cursor-not-allowed")}
        >
          {isValidating ? (
            <Loader2 className="h-4 w-4 mr-2 animate-spin" />
          ) : (
            <CheckCircle className="h-4 w-4 mr-2" />
          )}
          Valider et signer
        </Button>

        {hasCritique && (
          <span className="text-xs text-destructive flex items-center gap-1">
            <AlertTriangle className="h-3.5 w-3.5" />
            Alerte CRITIQUE non acquittée — signature bloquée
          </span>
        )}
      </div>
    </div>
  )
}

function AlertBadge({ alert }: { alert: Alert }) {
  const { variant, label } = alertStyle(alert.severity)
  return (
    <div className={cn("flex items-start gap-2 p-3 rounded-md border text-sm", alertBg(alert.severity))}>
      <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
      <div>
        <span className="font-medium">{label}</span>
        {" — "}
        <span>{alert.drug_a} + {alert.drug_b}</span>
        {alert.description && (
          <p className="text-xs mt-0.5 text-muted-foreground">{alert.description}</p>
        )}
      </div>
      <Badge variant={variant} className="ml-auto shrink-0">{alert.severity}</Badge>
    </div>
  )
}

function alertStyle(severity: Alert["severity"]): { variant: "destructive" | "warning" | "secondary"; label: string } {
  switch (severity) {
    case "CI_ABSOLUE":
    case "CRITIQUE":   return { variant: "destructive", label: "Contre-indication absolue" }
    case "CI_RELATIVE": return { variant: "warning",     label: "Contre-indication relative" }
    case "PRECAUTION":  return { variant: "warning",     label: "Précaution" }
    default:            return { variant: "secondary",   label: "Information" }
  }
}

function alertBg(severity: Alert["severity"]): string {
  switch (severity) {
    case "CI_ABSOLUE":
    case "CRITIQUE":   return "bg-red-50 border-red-200 text-red-800"
    case "CI_RELATIVE": return "bg-amber-50 border-amber-200 text-amber-800"
    default:            return "bg-slate-50 border-slate-200"
  }
}
