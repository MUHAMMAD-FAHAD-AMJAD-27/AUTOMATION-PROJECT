import { NextResponse } from "next/server";
import { getOverviewStats } from "@/lib/queries";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const stats = await getOverviewStats();
    // Every section failing means the database is unreachable; answering 200 with
    // a body full of nulls would let a consumer read it as "all quiet".
    const failed = Object.keys(stats.errors).length;
    if (failed >= 6) {
      return NextResponse.json(
        { error: stats.errors.activeOffers ?? "database unreachable", errors: stats.errors },
        { status: 503 }
      );
    }
    return NextResponse.json(stats);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "database error" },
      { status: 500 }
    );
  }
}