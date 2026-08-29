import "server-only";
import { Pool } from "pg";

/**
 * Server-only Postgres pool. Credentials live in DATABASE_URL (env) and are
 * never exposed to the client — components and API routes are the only
 * consumers, and every data page opts out of static prerendering.
 */
const globalForPg = globalThis as unknown as { __freebiesPool?: Pool };

export const pool =
  globalForPg.__freebiesPool ??
  new Pool({
    connectionString:
      process.env.DATABASE_URL ||
      "postgresql://freebies:***@localhost:5432/freebies",
    max: 5,
    idleTimeoutMillis: 30_000,
    connectionTimeoutMillis: 5_000,
    statement_timeout: 5_000,
  });

if (process.env.NODE_ENV !== "production") globalForPg.__freebiesPool = pool;

export type DbError = { ok: false; error: string };

/**
 * A failure message that is never empty.
 *
 * `err.message` alone is not enough: when every address a host resolves to
 * refuses the connection (localhost -> ::1 and 127.0.0.1), Node throws an
 * AggregateError whose own message is the EMPTY STRING and whose only readable
 * detail lives in `.errors`. An empty string is falsy, so it slips through every
 * `if (error)` and `filter(Boolean)` downstream and quietly restores the exact
 * failure mode the error surfacing exists to kill — panels with no data and no
 * complaint. Observed live: a dashboard pointed at an unreachable host answered
 * `{"error":"","errors":{"activeOffers":"", ...}}` and rendered as "no offers
 * recorded yet".
 */
function describeDbError(err: unknown): string {
  if (err instanceof AggregateError && Array.isArray(err.errors) && err.errors.length) {
    const causes = err.errors.map((e) => (e instanceof Error ? e.message : String(e)));
    const unique = Array.from(new Set(causes.filter(Boolean)));
    if (unique.length) return unique.join("; ");
  }
  if (err instanceof Error) {
    const code = (err as { code?: unknown }).code;
    if (err.message) return err.message;
    if (typeof code === "string" && code) return `${err.name}: ${code}`;
    if (err.name) return err.name;
  }
  return String(err) || "unknown database error";
}

export async function query<T extends Record<string, unknown>>(
  text: string,
  params: unknown[] = []
): Promise<{ ok: true; rows: T[] } | DbError> {
  try {
    const result = await pool.query<T>(text, params);
    return { ok: true, rows: result.rows };
  } catch (err) {
    return { ok: false, error: describeDbError(err) };
  }
}