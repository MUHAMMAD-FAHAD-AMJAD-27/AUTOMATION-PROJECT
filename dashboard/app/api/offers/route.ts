import { NextRequest, NextResponse } from "next/server";
import { getOffers } from "@/lib/queries";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const sp = request.nextUrl.searchParams;
  try {
    const rows = await getOffers({
      q: sp.get("q") ?? undefined,
      category: sp.get("category") ?? undefined,
      status: sp.get("status") ?? undefined,
      availability: (sp.get("availability") as "active" | "expired" | "all") ?? "active",
      limit: Math.min(Math.max(Number(sp.get("limit")) || 100, 1), 500),
    });
    // query() never throws, so the catch below cannot see a dead database. Without
    // this check the route answered 200 {"rows":[]} — indistinguishable from an
    // empty table to any consumer.
    if (rows.error) return NextResponse.json({ error: rows.error }, { status: 503 });
    return NextResponse.json({ rows: rows.rows });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "database error" },
      { status: 500 }
    );
  }
}