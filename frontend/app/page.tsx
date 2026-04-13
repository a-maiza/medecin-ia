import { redirect } from "next/navigation"
import { auth0 } from "@/lib/auth0"

export default async function HomePage() {
  const session = await auth0.getSession()
  if (session) {
    redirect("/dashboard")
  }
  redirect("/login")
}
