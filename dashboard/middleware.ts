import { NextRequest, NextResponse } from "next/server";
import { COOKIE_NAME, safeEqualHex, sha256Hex } from "@/lib/shared";

const PUBLIC_PATHS = new Set(["/login", "/api/login", "/api/logout"]);
const PUBLIC_PREFIXES = ["/_next/", "/favicon.ico"];

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (
    PUBLIC_PATHS.has(pathname) ||
    PUBLIC_PREFIXES.some((p) => pathname.startsWith(p))
  ) {
    return NextResponse.next();
  }

  const key =
    request.headers.get("x-api-key") ||
    request.cookies.get(COOKIE_NAME)?.value ||
    "";
  const expected = process.env.DASHBOARD_API_KEY || "";

  if (expected && key) {
    const [a, b] = await Promise.all([sha256Hex(key), sha256Hex(expected)]);
    if (safeEqualHex(a, b)) return NextResponse.next();
  }

  if (pathname.startsWith("/api")) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const login = new URL("/login", request.url);
  login.searchParams.set("next", pathname);
  return NextResponse.redirect(login);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};