import type { NextRequest } from "next/server"
import { auth0 } from "@/lib/auth0"

export async function middleware(request: NextRequest) {
  return await auth0.middleware(request)
}

export const config = {
  matcher: [
    // Protect all dashboard routes
    "/(dashboard)/:path*",
    "/(admin)/:path*",
    // Auth0 callback and logout routes must pass through
    "/api/auth/:path*",
  ],
}
