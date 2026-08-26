import { NextRequest, NextResponse } from "next/server";
import { verifyApiKey } from "@/lib/auth";
import { ingestManualDeal } from "@/lib/queries";

export const dynamic = "force-dynamic";

const MAX_TEXT = 4000;
const MAX_URLS = 8;

export async function POST(request: NextRequest) {
  // Defense in depth: middleware already gates /api, but a write route
  // double-checks so a middleware bypass can never insert rows.
  const headerKey =
    request.headers.get("x-api-key") ||
    request.cookies.get("dash_key")?.value ||
    "";
  if (!verifyApiKey(headerKey)) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  let body: { text?: unknown; urls?: unknown; authorHandle?: unknown };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid JSON body" }, { status: 400 });
  }

  if (typeof body.text !== "string" || body.text.trim().length < 10) {
    return NextResponse.json(
      { error: "text is required (min 10 characters)" },
      { status: 400 }
    );
  }
  if (body.text.length > MAX_TEXT) {
    return NextResponse.json(
      { error: `text exceeds ${MAX_TEXT} characters` },
      { status: 400 }
    );
  }

  const rawUrls = Array.isArray(body.urls) ? body.urls : [];
  if (rawUrls.length > MAX_URLS) {
    return NextResponse.json(
      { error: `at most ${MAX_URLS} URLs` },
      { status: 400 }
    );
  }
  const urls: string[] = [];
  for (const u of rawUrls) {
    if (typeof u !== "string" || !/^https?:\/\/\S+$/i.test(u.trim())) {
      return NextResponse.json({ error: "invalid URL in urls" }, { status: 400 });
    }
    urls.push(u.trim());
  }

  const authorHandle =
    typeof body.authorHandle === "string" && body.authorHandle.trim()
      ? body.authorHandle.trim().slice(0, 120)
      : undefined;

  const result = await ingestManualDeal({ text: body.text.trim(), urls, authorHandle });
  if (!result.ok) {
    return NextResponse.json({ error: result.error }, { status: 500 });
  }
  return NextResponse.json({ ok: true, id: result.id }, { status: 201 });
}