import "server-only";
import { createHash, timingSafeEqual } from "crypto";

export { COOKIE_NAME, COOKIE_MAX_AGE } from "./shared";

/**
 * Server-side API key check (node crypto, constant-time).
 * The edge middleware performs an equivalent check via Web Crypto;
 * routes double-verify so a middleware bypass still fails on writes.
 */
export function verifyApiKey(key: string | undefined | null): boolean {
  const expected = process.env.DASHBOARD_API_KEY || "";
  if (!expected || !key) return false;
  const a = createHash("sha256").update(key).digest();
  const b = createHash("sha256").update(expected).digest();
  return timingSafeEqual(a, b);
}