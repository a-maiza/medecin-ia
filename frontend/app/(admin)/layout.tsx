import { redirect } from "next/navigation"
import Link from "next/link"
import { BookOpen, LayoutDashboard, LogOut } from "lucide-react"

async function getSession() {
  try {
    const { auth0 } = await import("@/lib/auth0")
    return await auth0.getSession()
  } catch {
    return null
  }
}

const adminNav = [
  { href: "/admin/dashboard",       label: "Tableau de bord admin", icon: LayoutDashboard },
  { href: "/admin/knowledge-base",  label: "Base globale",          icon: BookOpen },
]

export default async function AdminLayout({ children }: { children: React.ReactNode }) {
  const session = await getSession()
  if (!session) redirect("/login")

  // Role guard — admin_medecinai only
  const role = (session.user as Record<string, unknown>)?.role ?? ""
  if (role !== "admin_medecinai") redirect("/dashboard")

  return (
    <div className="flex h-screen bg-background">
      <aside className="w-64 flex-shrink-0 border-r bg-white flex flex-col">
        <div className="px-6 py-5 border-b">
          <span className="text-xl font-bold text-primary">MédecinAI</span>
          <span className="ml-2 text-xs text-muted-foreground font-medium uppercase tracking-wide">Admin</span>
        </div>
        <nav className="flex-1 px-3 py-4 space-y-1">
          {adminNav.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className="flex items-center gap-3 px-3 py-2 rounded-md text-sm text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors"
            >
              <Icon className="h-4 w-4 shrink-0" />
              {label}
            </Link>
          ))}
        </nav>
        <div className="px-3 py-4 border-t">
          <a
            href="/api/auth/logout"
            className="flex items-center gap-3 px-3 py-2 rounded-md text-sm text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors"
          >
            <LogOut className="h-4 w-4 shrink-0" />
            Déconnexion
          </a>
        </div>
      </aside>
      <main className="flex-1 overflow-y-auto">{children}</main>
    </div>
  )
}
