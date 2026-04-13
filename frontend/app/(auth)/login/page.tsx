import Link from "next/link"
import { Button } from "@/components/ui/button"

export default function LoginPage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-blue-50 to-white">
      <div className="w-full max-w-md px-8 py-10 bg-white rounded-2xl shadow-md border border-border">
        {/* Logo / brand */}
        <div className="mb-8 text-center">
          <h1 className="text-2xl font-bold text-primary">MédecinAI</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Assistant IA pour médecins généralistes
          </p>
        </div>

        <h2 className="text-xl font-semibold mb-6 text-center">Connexion</h2>

        {/* Auth0 handles the full login flow — we redirect to its endpoint */}
        <Button asChild className="w-full" size="lg">
          <a href="/api/auth/login">Se connecter avec Auth0</a>
        </Button>

        {/* e-CPS shortcut (Pro Santé Connect — P1) */}
        <Button asChild variant="outline" className="w-full mt-3" size="lg">
          <a href="/api/auth/login?connection=pro-sante-connect">
            Se connecter avec e-CPS
          </a>
        </Button>

        <p className="mt-6 text-center text-sm text-muted-foreground">
          Pas encore de compte ?{" "}
          <Link href="/register" className="text-primary font-medium hover:underline">
            Créer un compte
          </Link>
        </p>
      </div>
    </div>
  )
}
