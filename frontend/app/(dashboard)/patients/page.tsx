"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { Plus, Search, User } from "lucide-react"
import { api } from "@/lib/api"
import type { Patient } from "@/lib/types"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Badge } from "@/components/ui/badge"

export default function PatientsPage() {
  const [patients, setPatients] = useState<Patient[]>([])
  const [searchQuery, setSearchQuery] = useState("")
  const [isLoading, setIsLoading] = useState(true)
  const [isCreating, setIsCreating] = useState(false)
  const [dialogOpen, setDialogOpen] = useState(false)

  // New patient form state
  const [form, setForm] = useState({
    nom: "",
    date_naissance: "",
    sexe: "" as "M" | "F" | "autre" | "",
    ins: "",
    dfg: "",
    grossesse: false,
  })

  useEffect(() => {
    loadPatients()
  }, [])

  async function loadPatients(query?: string) {
    setIsLoading(true)
    try {
      const url = query
        ? `/patients/search?nom=${encodeURIComponent(query)}`
        : "/patients?limit=100"
      const { data } = await api.get<Patient[]>(url)
      setPatients(data)
    } catch {
      setPatients([])
    } finally {
      setIsLoading(false)
    }
  }

  function handleSearch(e: React.FormEvent) {
    e.preventDefault()
    loadPatients(searchQuery.trim() || undefined)
  }

  async function handleCreatePatient(e: React.FormEvent) {
    e.preventDefault()
    setIsCreating(true)
    try {
      await api.post("/patients", {
        nom: form.nom,
        date_naissance: form.date_naissance,
        sexe: form.sexe || undefined,
        ins: form.ins || undefined,
        dfg: form.dfg ? parseFloat(form.dfg) : undefined,
        grossesse: form.grossesse,
      })
      setDialogOpen(false)
      setForm({ nom: "", date_naissance: "", sexe: "", ins: "", dfg: "", grossesse: false })
      loadPatients()
    } catch {
      alert("Erreur lors de la création du patient.")
    } finally {
      setIsCreating(false)
    }
  }

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Patients</h1>
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogTrigger asChild>
            <Button>
              <Plus className="h-4 w-4 mr-2" />
              Nouveau patient
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Créer un dossier patient</DialogTitle>
            </DialogHeader>
            <form onSubmit={handleCreatePatient} className="space-y-4 mt-2">
              <div>
                <Label htmlFor="nom">Nom complet *</Label>
                <Input
                  id="nom"
                  required
                  value={form.nom}
                  onChange={(e) => setForm({ ...form, nom: e.target.value })}
                  className="mt-1"
                />
              </div>
              <div>
                <Label htmlFor="dob">Date de naissance *</Label>
                <Input
                  id="dob"
                  type="date"
                  required
                  value={form.date_naissance}
                  onChange={(e) => setForm({ ...form, date_naissance: e.target.value })}
                  className="mt-1"
                />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <Label>Sexe</Label>
                  <Select
                    value={form.sexe}
                    onValueChange={(v) => setForm({ ...form, sexe: v as typeof form.sexe })}
                  >
                    <SelectTrigger className="mt-1">
                      <SelectValue placeholder="—" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="M">Masculin</SelectItem>
                      <SelectItem value="F">Féminin</SelectItem>
                      <SelectItem value="autre">Autre</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <Label htmlFor="dfg">DFG (mL/min)</Label>
                  <Input
                    id="dfg"
                    type="number"
                    min="0"
                    max="200"
                    value={form.dfg}
                    onChange={(e) => setForm({ ...form, dfg: e.target.value })}
                    className="mt-1"
                  />
                </div>
              </div>
              <div>
                <Label htmlFor="ins">Numéro INS (optionnel)</Label>
                <Input
                  id="ins"
                  maxLength={22}
                  value={form.ins}
                  onChange={(e) => setForm({ ...form, ins: e.target.value })}
                  className="mt-1"
                />
              </div>
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="grossesse"
                  checked={form.grossesse}
                  onChange={(e) => setForm({ ...form, grossesse: e.target.checked })}
                />
                <Label htmlFor="grossesse">Grossesse en cours</Label>
              </div>
              <div className="flex gap-3 pt-2">
                <Button type="submit" disabled={isCreating} className="flex-1">
                  {isCreating ? "Création…" : "Créer le dossier"}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setDialogOpen(false)}
                >
                  Annuler
                </Button>
              </div>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      {/* Search */}
      <form onSubmit={handleSearch} className="flex gap-2 mb-6 max-w-md">
        <Input
          placeholder="Rechercher par nom ou INS…"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
        />
        <Button type="submit" variant="outline" size="icon">
          <Search className="h-4 w-4" />
        </Button>
      </form>

      {/* Patient list */}
      {isLoading ? (
        <p className="text-muted-foreground">Chargement…</p>
      ) : patients.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            Aucun patient trouvé.
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {patients.map((p) => (
            <Link key={p.id} href={`/patients/${p.id}`}>
              <Card className="hover:shadow-md transition-shadow cursor-pointer">
                <CardHeader className="pb-2">
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-2">
                      <User className="h-5 w-5 text-muted-foreground shrink-0" />
                      <CardTitle className="text-base">{p.nom}</CardTitle>
                    </div>
                    {p.grossesse && <Badge variant="warning">Grossesse</Badge>}
                  </div>
                </CardHeader>
                <CardContent>
                  <div className="text-sm text-muted-foreground space-y-1">
                    {p.ins && <p>INS : {p.ins}</p>}
                    {p.dfg != null && (
                      <p>
                        DFG :{" "}
                        <span className={p.dfg < 30 ? "text-red-600 font-medium" : p.dfg < 60 ? "text-amber-600 font-medium" : ""}>
                          {p.dfg} mL/min
                        </span>
                      </p>
                    )}
                    {p.allergies.length > 0 && (
                      <p>Allergies : {p.allergies.slice(0, 2).join(", ")}{p.allergies.length > 2 ? "…" : ""}</p>
                    )}
                  </div>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
