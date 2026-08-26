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

export async function query<T extends Record<string, unknown>>(
  text: string,
  params: unknown[] = []
): Promise<{ ok: true; rows: T[] } | DbError> {
  try {
    const result = await pool.query<T>(text, params);
    return { ok: true, rows: result.rows };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return { ok: false, error: message };
  }
}