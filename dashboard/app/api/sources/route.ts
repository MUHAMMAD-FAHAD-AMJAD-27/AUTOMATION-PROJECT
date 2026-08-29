import { NextResponse } from "next/server";
import { getSources } from "@/lib/queries";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const data = await getSources();
    if (Object.keys(data.errors).length >= 4) {
      return NextResponse.json(
        { error: data.errors.sources ?? "database unreachable", errors: data.errors },
        { status: 503 }
      );
    }
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "database error" },
      { status: 500 }
    );
  }
}