"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { api } from "@/lib/api"

interface RegisterFormData {
  rpps: string
  nom: string
  prenom: string
  specialite: string
  email: string
  nom_cabinet: string
  adresse_cabinet: string
  pays: "FR" | "DZ"
  siret: string
}

const SPECIALITES = [
  "Médecine générale",
  "Cardiologie",
  "Dermatologie",
  "Gynécologie-obstétrique",
  "Pédiatrie",
  "Psychiatrie",
  "Chirurgie générale",
  "Ophtalmologie",
  "ORL",
  "Rhumatologie",
  "Neurologie",
  "Autre",
]

export default function RegisterPage() {
  const router = useRouter()
  const [form, setForm] = useState<RegisterFormData>({
    rpps: "",
    nom: "",
    prenom: "",
    specialite: "Médecine générale",
    email: "",
    nom_cabinet: "",
    adresse_cabinet: "",
    pays: "FR",
    siret: "",
  })
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const handleChange = (
    e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>
  ) => {
    setForm((prev) => ({ ...prev, [e.target.name]: e.target.value }))
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setLoading(true)

    try {
      await api.post("/auth/register", {
        ...form,
        siret: form.siret || undefined,
      })
      // Redirect to onboarding after successful registration
      router.push("/onboarding")
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        "Une erreur est survenue. Veuillez réessayer."
      setError(message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-blue-50 to-white py-10">
      <div className="w-full max-w-lg px-8 py-10 bg-white rounded-2xl shadow-md border border-border">
        <div className="mb-6 text-center">
          <h1 className="text-2xl font-bold text-primary">MédecinAI</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Créer votre compte médecin
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Médecin info */}
          <fieldset className="border border-border rounded-lg p-4 space-y-3">
            <legend className="text-sm font-semibold px-1">Informations personnelles</legend>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label htmlFor="prenom">Prénom</Label>
                <Input id="prenom" name="prenom" value={form.prenom} onChange={handleChange} required />
              </div>
              <div className="space-y-1">
                <Label htmlFor="nom">Nom</Label>
                <Input id="nom" name="nom" value={form.nom} onChange={handleChange} required />
              </div>
            </div>

            <div className="space-y-1">
              <Label htmlFor="rpps">Numéro RPPS (11 chiffres)</Label>
              <Input
                id="rpps"
                name="rpps"
                value={form.rpps}
                onChange={handleChange}
                pattern="\d{11}"
                maxLength={11}
                placeholder="12345678901"
                required
              />
            </div>

            <div className="space-y-1">
              <Label htmlFor="email">Email professionnel</Label>
              <Input
                id="email"
                name="email"
                type="email"
                value={form.email}
                onChange={handleChange}
                required
              />
            </div>

            <div className="space-y-1">
              <Label htmlFor="specialite">Spécialité</Label>
              <select
                id="specialite"
                name="specialite"
                value={form.specialite}
                onChange={handleChange}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                required
              >
                {SPECIALITES.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </div>
          </fieldset>

          {/* Cabinet info */}
          <fieldset className="border border-border rounded-lg p-4 space-y-3">
            <legend className="text-sm font-semibold px-1">Cabinet médical</legend>

            <div className="space-y-1">
              <Label htmlFor="nom_cabinet">Nom du cabinet</Label>
              <Input id="nom_cabinet" name="nom_cabinet" value={form.nom_cabinet} onChange={handleChange} required />
            </div>

            <div className="space-y-1">
              <Label htmlFor="adresse_cabinet">Adresse</Label>
              <Input id="adresse_cabinet" name="adresse_cabinet" value={form.adresse_cabinet} onChange={handleChange} required />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label htmlFor="pays">Pays</Label>
                <select
                  id="pays"
                  name="pays"
                  value={form.pays}
                  onChange={handleChange}
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <option value="FR">France</option>
                  <option value="DZ">Algérie</option>
                </select>
              </div>
              <div className="space-y-1">
                <Label htmlFor="siret">SIRET (optionnel)</Label>
                <Input
                  id="siret"
                  name="siret"
                  value={form.siret}
                  onChange={handleChange}
                  pattern="\d{14}"
                  maxLength={14}
                  placeholder="12345678901234"
                />
              </div>
            </div>
          </fieldset>

          {/* CGU */}
          <div className="flex items-start gap-2">
            <input type="checkbox" id="cgu" className="mt-1" required />
            <Label htmlFor="cgu" className="text-sm font-normal leading-snug">
              J'accepte les{" "}
              <a href="/cgu" target="_blank" className="text-primary hover:underline">
                Conditions Générales d'Utilisation
              </a>{" "}
              et la{" "}
              <a href="/confidentialite" target="_blank" className="text-primary hover:underline">
                Politique de confidentialité
              </a>
            </Label>
          </div>

          {error && (
            <p className="text-sm text-destructive bg-destructive/10 px-3 py-2 rounded-md">
              {error}
            </p>
          )}

          <Button type="submit" className="w-full" size="lg" disabled={loading}>
            {loading ? "Création du compte…" : "Créer mon compte"}
          </Button>
        </form>

        <p className="mt-5 text-center text-sm text-muted-foreground">
          Déjà un compte ?{" "}
          <Link href="/login" className="text-primary font-medium hover:underline">
            Se connecter
          </Link>
        </p>
      </div>
    </div>
  )
}
