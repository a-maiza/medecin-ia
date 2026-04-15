"use client"

import { useEffect, useState } from "react"
import { Eye, EyeOff, ExternalLink } from "lucide-react"
import { api } from "@/lib/api"
import type { Medecin } from "@/lib/types"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"

export default function SettingsPage() {
  const [medecin, setMedecin] = useState<Medecin | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [doctolibToken, setDoctolibToken] = useState("")
  const [showToken, setShowToken] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [isCreatingCheckout, setIsCreatingCheckout] = useState(false)
  const [isCreatingPortal, setIsCreatingPortal] = useState(false)

  useEffect(() => {
    api.get<Medecin>("/auth/me").then((r) => {
      setMedecin(r.data)
    }).catch(() => {}).finally(() => setIsLoading(false))
  }, [])

  async function handleSaveDoctolibToken(e: React.FormEvent) {
    e.preventDefault()
    setIsSaving(true)
    try {
      // Token saved as a doctor preference via PATCH endpoint (to be wired)
      await api.patch("/auth/me/preferences", { doctolib_token: doctolibToken })
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    } catch {
      alert("Erreur lors de la sauvegarde.")
    } finally {
      setIsSaving(false)
    }
  }

  async function handleUpgradeCheckout(plan: string) {
    setIsCreatingCheckout(true)
    try {
      const { data } = await api.post<{ checkout_url: string }>("/billing/checkout", {
        plan,
        success_url: `${window.location.origin}/settings?upgraded=1`,
        cancel_url:  `${window.location.origin}/settings`,
      })
      window.location.href = data.checkout_url
    } catch {
      alert("Erreur lors de la création de la session de paiement.")
    } finally {
      setIsCreatingCheckout(false)
    }
  }

  async function handleManageSubscription() {
    setIsCreatingPortal(true)
    try {
      const { data } = await api.post<{ portal_url: string }>("/billing/portal", {
        return_url: `${window.location.origin}/settings`,
      })
      window.location.href = data.portal_url
    } catch {
      alert("Erreur lors de l'accès au portail d'abonnement.")
    } finally {
      setIsCreatingPortal(false)
    }
  }

  if (isLoading) return <div className="p-6 text-muted-foreground">Chargement…</div>

  const trialEnds = medecin?.trial_ends_at ? new Date(medecin.trial_ends_at) : null
  const isOnTrial = trialEnds ? trialEnds > new Date() : false

  return (
    <div className="p-6 max-w-2xl">
      <h1 className="text-2xl font-bold mb-6">Paramètres</h1>

      {/* ── Profil médecin ───────────────────────────────────────────────────── */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle>Profil médecin</CardTitle>
          <CardDescription>Informations de votre compte MédecinAI</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {medecin && (
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <p className="text-muted-foreground">Nom</p>
                <p className="font-medium">Dr {medecin.prenom} {medecin.nom}</p>
              </div>
              <div>
                <p className="text-muted-foreground">RPPS</p>
                <p className="font-mono font-medium">{medecin.rpps}</p>
              </div>
              <div>
                <p className="text-muted-foreground">Spécialité</p>
                <p className="font-medium">{medecin.specialite}</p>
              </div>
              <div>
                <p className="text-muted-foreground">Email</p>
                <p className="font-medium">{medecin.email}</p>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* ── Intégration Doctolib ─────────────────────────────────────────────── */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle>Intégration Doctolib</CardTitle>
          <CardDescription>
            Activez la synchronisation automatique des comptes-rendus vers Doctolib.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSaveDoctolibToken} className="space-y-4">
            <div>
              <Label htmlFor="doctolib-token">Token API Doctolib</Label>
              <div className="flex gap-2 mt-1">
                <div className="relative flex-1">
                  <Input
                    id="doctolib-token"
                    type={showToken ? "text" : "password"}
                    value={doctolibToken}
                    onChange={(e) => setDoctolibToken(e.target.value)}
                    placeholder="Collez votre token Doctolib ici…"
                    className="pr-10"
                  />
                  <button
                    type="button"
                    onClick={() => setShowToken(!showToken)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                  >
                    {showToken ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
              </div>
            </div>
            <Button type="submit" disabled={isSaving || !doctolibToken.trim()}>
              {saved ? "Sauvegardé ✓" : isSaving ? "Sauvegarde…" : "Sauvegarder"}
            </Button>
          </form>
        </CardContent>
      </Card>

      {/* ── Abonnement ───────────────────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle>Abonnement</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Current plan */}
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium">Plan actuel</p>
              {isOnTrial ? (
                <p className="text-sm text-muted-foreground">
                  Période d&apos;essai — expire le{" "}
                  <strong>{trialEnds!.toLocaleDateString("fr-FR")}</strong>
                </p>
              ) : (
                <p className="text-sm text-muted-foreground">Abonnement actif</p>
              )}
            </div>
            <Badge variant={isOnTrial ? "warning" : "success"}>
              {isOnTrial ? "Essai" : "Actif"}
            </Badge>
          </div>

          <Separator />

          {/* Plans */}
          <div className="space-y-3">
            <p className="text-sm font-medium">Choisir un plan</p>
            {PLANS.map((plan) => (
              <div key={plan.id} className="flex items-center justify-between p-3 rounded-lg border">
                <div>
                  <p className="text-sm font-medium">{plan.name}</p>
                  <p className="text-xs text-muted-foreground">{plan.description}</p>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-sm font-bold">{plan.price}</span>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => handleUpgradeCheckout(plan.id)}
                    disabled={isCreatingCheckout}
                  >
                    <ExternalLink className="h-3.5 w-3.5 mr-1.5" />
                    Souscrire
                  </Button>
                </div>
              </div>
            ))}
          </div>

          <Separator />

          <Button
            variant="outline"
            onClick={handleManageSubscription}
            disabled={isCreatingPortal}
          >
            {isCreatingPortal ? "Redirection…" : "Gérer / annuler mon abonnement"}
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}

const PLANS = [
  { id: "solo",    name: "Plan Solo",    price: "150 €/mois", description: "1 médecin, toutes les fonctionnalités" },
  { id: "cabinet", name: "Plan Cabinet", price: "Sur devis",   description: "2–10 médecins, cabinet partagé" },
  { id: "reseau",  name: "Plan Réseau",  price: "Sur devis",   description: "Groupement de cabinets, multi-sites" },
]
