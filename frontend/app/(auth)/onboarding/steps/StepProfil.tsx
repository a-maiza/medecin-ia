"use client"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

interface StepProfilProps {
  onNext: () => void
}

export default function StepProfil({ onNext }: StepProfilProps) {
  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold">Votre profil médecin</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Confirmez vos informations pour personnaliser votre expérience.
        </p>
      </div>

      <div className="space-y-3">
        <div className="space-y-1">
          <Label>Spécialité principale</Label>
          <Input defaultValue="Médecine générale" readOnly className="bg-muted/30" />
        </div>
        <div className="space-y-1">
          <Label>Numéro RPPS</Label>
          <Input placeholder="Renseigné lors de l'inscription" readOnly className="bg-muted/30" />
        </div>
        <div className="space-y-1">
          <Label>Préférences de génération SOAP</Label>
          <select className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <option>Style concis (recommandé)</option>
            <option>Style détaillé</option>
          </select>
        </div>
      </div>

      <Button onClick={onNext} className="w-full">
        Continuer
      </Button>
    </div>
  )
}
