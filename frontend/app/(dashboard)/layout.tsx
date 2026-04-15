import Link from "next/link"
import { redirect } from "next/navigation"
import {
  Activity,
  BookOpen,
  LayoutDashboard,
  LogOut,
  Settings,
  Users,
} from "lucide-react"

// Auth0 server-side session check
async function getSession() {
  try {
    // Dynamic import to avoid SSR issues
    const { auth0 } = await import("@/lib/auth0")
    return await auth0.getSession()
  } catch {
    return null
  }
}

const navItems = [
  { href: "/dashboard",                  label: "Tableau de bord", icon: LayoutDashboard },
  { href: "/consultation/new",           label: "Nouvelle consultation", icon: Activity },
  { href: "/patients",                   label: "Patients",        icon: Users },
  { href: "/knowledge-base",             label: "Base de connaissances", icon: BookOpen },
  { href: "/settings",                   label: "Paramètres",      icon: Settings },
]

export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  const session = await getSession()
  if (!session) redirect("/login")

  return (
    <div className="flex h-screen bg-background">
      {/* ── Sidebar ─────────────────────────────────────────────────────────── */}
      <aside className="w-64 flex-shrink-0 border-r bg-white flex flex-col">
        {/* Brand */}
        <div className="px-6 py-5 border-b">
          <span className="text-xl font-bold text-primary">MédecinAI</span>
        </div>

        {/* Navigation */}
        <nav className="flex-1 px-3 py-4 space-y-1">
          {navItems.map(({ href, label, icon: Icon }) => (
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

        {/* User / logout */}
        <div className="px-3 py-4 border-t">
          <a
            href="/api/auth/logout"
            className="flex items-center gap-3 px-3 py-2 rounded-md text-sm text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors w-full"
          >
            <LogOut className="h-4 w-4 shrink-0" />
            Déconnexion
          </a>
        </div>
      </aside>

      {/* ── Main content ────────────────────────────────────────────────────── */}
      <main className="flex-1 overflow-y-auto">
        {children}
      </main>
    </div>
  )
}
