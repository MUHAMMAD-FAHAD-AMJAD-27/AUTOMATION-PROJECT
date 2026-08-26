import type { VerificationStatus } from "@/lib/queries";
// Category palette is generated from crawler/categories.py — see scripts/sync_categories.py.
// Imported from the plain module (not queries.ts, which is server-only).
import { CATEGORY_COLORS } from "@/lib/categories";

export { CATEGORY_COLORS };

export function CategoryBadge({ category }: { category: string }) {
  const color = CATEGORY_COLORS[category] ?? CATEGORY_COLORS.other;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-raised px-2 py-0.5 text-[11px] text-fg/90">
      <span
        className="h-1.5 w-1.5 rounded-full"
        style={{ backgroundColor: color }}
        aria-hidden
      />
      {category}
    </span>
  );
}

const STATUS_TONES: Record<VerificationStatus, { label: string; classes: string }> = {
  verified: { label: "verified", classes: "bg-success/10 text-success border-success/25" },
  live: { label: "live (gated)", classes: "bg-accent-soft text-accent border-accent/25" },
  unconfirmed: { label: "unconfirmed", classes: "bg-warn/10 text-warn border-warn/25" },
  expired: { label: "expired", classes: "bg-muted/10 text-muted border-border" },
  dead: { label: "dead", classes: "bg-danger/10 text-danger border-danger/25" },
  reported: { label: "reported", classes: "bg-danger/10 text-danger border-danger/25" },
};

export function StatusBadge({ status }: { status: string }) {
  const tone = STATUS_TONES[status as VerificationStatus] ?? {
    label: status,
    classes: "bg-raised text-muted border-border",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] ${tone.classes}`}
    >
      {tone.label}
    </span>
  );
}

export function SourceKindBadge({ kind }: { kind: string }) {
  return (
    <span className="inline-flex items-center rounded border border-border bg-raised px-1.5 py-0.5 font-mono text-[10px] tracking-caps text-muted uppercase">
      {kind}
    </span>
  );
}