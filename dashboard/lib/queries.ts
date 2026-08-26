import "server-only";
import { query } from "./db";

// Taxonomy is generated from crawler/categories.py — see scripts/sync_categories.py.
export { CATEGORIES, OFFER_TYPES, CATEGORY_COLORS, CATEGORY_EMOJIS } from "./categories";
export type { Category, OfferType } from "./categories";

export const VERIFICATION_STATUSES = [
  "verified", "live", "unconfirmed", "expired", "dead", "reported",
] as const;
export type VerificationStatus = (typeof VERIFICATION_STATUSES)[number];

// --------------------------------------------------------------------------- //
// Overview
// --------------------------------------------------------------------------- //
export type OverviewStats = {
  activeOffers: number;
  pendingQueue: number;
  totalDispatches: number;
  lastRun: { status: string; startedAt: string | null; finishedAt: string | null; stats: unknown } | null;
  categoryCounts: { category: string; count: number }[];
  last7Days: { day: string; count: number }[];
};

export async function getOverviewStats(): Promise<OverviewStats> {
  const [offers, queue, dispatched, run, cats, week] = await Promise.all([
    query<{ count: number }>(
      `SELECT COUNT(*)::int AS count FROM offers
       WHERE is_active AND verification_status IN ('verified','live')`
    ),
    query<{ count: number }>(
      `SELECT COUNT(*)::int AS count FROM raw_items r
       WHERE NOT EXISTS (SELECT 1 FROM offers o WHERE o.raw_item_id = r.id)`
    ),
    query<{ count: number }>(
      `SELECT COUNT(*)::int AS count FROM dispatches WHERE status = 'sent'`
    ),
    query<{ status: string; started_at: Date; finished_at: Date | null; stats: unknown }>(
      `SELECT status, started_at, finished_at, stats FROM runs
       ORDER BY started_at DESC LIMIT 1`
    ),
    query<{ category: string; count: number }>(
      `SELECT category, COUNT(*)::int AS count FROM offers
       WHERE is_active GROUP BY category ORDER BY count DESC, category`
    ),
    query<{ day: Date; count: number }>(
      `SELECT date_trunc('day', first_seen)::date AS day, COUNT(*)::int AS count
       FROM offers WHERE first_seen > now() - interval '7 days'
       GROUP BY 1 ORDER BY 1`
    ),
  ]);

  return {
    activeOffers: offers.ok ? offers.rows[0]?.count ?? 0 : 0,
    pendingQueue: queue.ok ? queue.rows[0]?.count ?? 0 : 0,
    totalDispatches: dispatched.ok ? dispatched.rows[0]?.count ?? 0 : 0,
    lastRun: run.ok
      ? run.rows[0]
        ? { status: run.rows[0].status, startedAt: run.rows[0].started_at?.toISOString() ?? null, finishedAt: run.rows[0].finished_at?.toISOString() ?? null, stats: run.rows[0].stats }
        : null
      : null,
    categoryCounts: (cats.ok ? cats.rows : []).map((r) => ({
      category: r.category,
      count: r.count,
    })),
    last7Days: (week.ok ? week.rows : []).map((r) => ({
      day: r.day.toISOString().slice(0, 10),
      count: r.count,
    })),
  };
}

// --------------------------------------------------------------------------- //
// Offers feed
// --------------------------------------------------------------------------- //
export type OfferFilters = {
  q?: string;
  category?: string;
  status?: string;
  availability?: "active" | "expired" | "all";
  limit?: number;
};

export type OfferRow = {
  id: number;
  title: string;
  url: string;
  category: string;
  offer_type: string;
  value: number | null;
  currency: string | null;
  expires_at: string | null;
  confidence: number | null;
  verification_status: string;
  is_active: boolean;
  first_seen: string;
  source_name: string | null;
};

export async function getOffers(filters: OfferFilters = {}): Promise<OfferRow[]> {
  const clauses: string[] = [];
  const params: unknown[] = [];

  if (filters.q) {
    params.push(`%${filters.q}%`);
    clauses.push(`o.title ILIKE $${params.length}`);
  }
  if (filters.category && filters.category !== "all") {
    params.push(filters.category);
    clauses.push(`o.category = $${params.length}`);
  }
  if (filters.status && filters.status !== "all") {
    params.push(filters.status);
    clauses.push(`o.verification_status = $${params.length}`);
  }
  if (filters.availability === "active") {
    clauses.push(`o.is_active = true`);
  } else if (filters.availability === "expired") {
    clauses.push(`(o.is_active = false OR (o.expires_at IS NOT NULL AND o.expires_at < now()))`);
  }

  const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
  params.push(filters.limit ?? 100);
  const sql = `
    SELECT o.id, o.title, o.url, o.category, o.offer_type,
           o.value::float8 AS value, o.currency,
           o.expires_at::text AS expires_at,
           o.confidence, o.verification_status, o.is_active,
           o.first_seen::text AS first_seen,
           s.name AS source_name
    FROM offers o
    LEFT JOIN raw_items r ON r.id = o.raw_item_id
    LEFT JOIN sources s ON s.id = r.source_id
    ${where}
    ORDER BY o.first_seen DESC
    LIMIT $${params.length}
  `;
  const result = await query<OfferRow>(sql, params);
  return result.ok ? result.rows : [];
}

// --------------------------------------------------------------------------- //
// Sources monitor
// --------------------------------------------------------------------------- //
export type SourceRow = {
  id: number;
  name: string;
  kind: string;
  enabled: boolean;
  health: unknown;
  updated_at: string | null;
};

export type CursorRow = {
  source_name: string;
  channel_username: string;
  last_message_id: number;
  updated_at: string | null;
};

export type DiscoveredRow = { status: string; count: number };

export async function getSources(): Promise<{
  sources: SourceRow[];
  cursors: CursorRow[];
  discovered: DiscoveredRow[];
  recentRuns: { flow_key: string; status: string; started_at: string | null }[];
}> {
  const [sources, cursors, discovered, runs] = await Promise.all([
    query<SourceRow>(
      `SELECT id, name, kind, enabled, health, updated_at::text AS updated_at
       FROM sources ORDER BY name`
    ),
    query<CursorRow>(
      `SELECT s.name AS source_name, c.channel_username, c.last_message_id,
              c.updated_at::text AS updated_at
       FROM channel_cursors c JOIN sources s ON s.id = c.source_id
       ORDER BY c.updated_at DESC NULLS LAST`
    ),
    query<DiscoveredRow>(
      `SELECT status, COUNT(*)::int AS count FROM discovered_channels GROUP BY status`
    ),
    query<{ flow_key: string; status: string; started_at: Date }>(
      `SELECT flow_key, status, started_at FROM runs
       ORDER BY started_at DESC LIMIT 5`
    ),
  ]);

  return {
    sources: sources.ok ? sources.rows : [],
    cursors: cursors.ok ? cursors.rows : [],
    discovered: discovered.ok ? discovered.rows : [],
    recentRuns: (runs.ok ? runs.rows : []).map((r) => ({
      flow_key: r.flow_key,
      status: r.status,
      started_at: r.started_at.toISOString(),
    })),
  };
}

// --------------------------------------------------------------------------- //
// Manual ingestion
// --------------------------------------------------------------------------- //
export type IngestInput = {
  text: string;
  urls?: string[];
  authorHandle?: string;
};

export async function ingestManualDeal(input: IngestInput): Promise<
  { ok: true; id: number; rawItemId: number } | { ok: false; error: string }
> {
  const source = await query<{ id: number }>(
    `INSERT INTO sources (name, kind, config)
     VALUES ('manual:dashboard', 'manual', '{}')
     ON CONFLICT (name) DO UPDATE SET kind = EXCLUDED.kind
     RETURNING id`
  );
  if (!source.ok || !source.rows[0]) return { ok: false, error: "source upsert failed" };
  const sourceId = source.rows[0].id;

  const externalId = `manual-${Date.now()}`;
  const payload = {
    text: input.text,
    urls: (input.urls || []).filter(Boolean),
    author_handle: input.authorHandle || "manual:paste",
    published_at: new Date().toISOString(),
    engagement: {},
    extra: { origin: "dashboard-manual-ingest" },
  };

  const inserted = await query<{ id: number }>(
    `INSERT INTO raw_items (source_id, external_id, raw_payload)
     VALUES ($1, $2, $3)
     ON CONFLICT (source_id, external_id) DO NOTHING
     RETURNING id`,
    [sourceId, externalId, JSON.stringify(payload)]
  );
  if (!inserted.ok) return { ok: false, error: inserted.error };
  if (!inserted.rows[0]) return { ok: false, error: "duplicate external id" };
  return { ok: true, id: inserted.rows[0].id, rawItemId: inserted.rows[0].id };
}