/** Small display helpers shared by server components (no client deps). */

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function expiresIn(iso: string | null | undefined): {
  label: string;
  state: "expired" | "soon" | "ok" | "none";
} {
  if (!iso) return { label: "no expiry", state: "none" };
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return { label: "unknown", state: "none" };
  const diff = then - Date.now();
  if (diff < 0) {
    const days = Math.ceil(Math.abs(diff) / 86_400_000);
    return { label: `expired ${days}d ago`, state: "expired" };
  }
  const days = Math.ceil(diff / 86_400_000);
  if (days <= 3) return { label: `in ${days} day${days === 1 ? "" : "s"}`, state: "soon" };
  return { label: `in ${days} days`, state: "ok" };
}

export function formatValue(value: number | null, currency: string | null): string {
  if (value === null || value === undefined) return "—";
  const formatted = new Intl.NumberFormat("en-US", {
    maximumFractionDigits: value % 1 === 0 ? 0 : 2,
  }).format(value);
  return currency ? `${formatted} ${currency}` : formatted;
}

export function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return text.slice(0, max - 1).trimEnd() + "…";
}