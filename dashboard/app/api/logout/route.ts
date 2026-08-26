import { NextResponse } from "next/server";
import { COOKIE_NAME } from "@/lib/shared";

export async function GET() {
  const response = NextResponse.redirect(new URL("/login", "http://localhost"));
  response.cookies.set(COOKIE_NAME, "", {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: 0,
  });
  return response;
}