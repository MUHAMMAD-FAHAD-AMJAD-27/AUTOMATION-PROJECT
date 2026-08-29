import type { ReactNode } from "react";
import { AlertTriangle } from "lucide-react";

export function StatCard({
  label,
  value,
  hint,
  icon,
  iconClass = "text-muted",
  error,
}: {
  label: string;
  /** `null` renders as "—": the value could not be read. Never pass "0" for that. */
  value: string | null;
  hint: string;
  icon: ReactNode;
  iconClass?: string;
  /** When set, the card shows the failure instead of a plausible-looking number. */
  error?: string;
}) {
  const failed = Boolean(error);
  return (
    <div
      className={`rounded-xl border bg-surface p-4 ${
        failed ? "border-danger/30" : "border-border"
      }`}
      {...(failed ? { role: "alert" as const } : {})}
    >
      <div className="flex items-center justify-between">
        <span className="text-[11px] tracking-caps text-muted uppercase">{label}</span>
        <span className={failed ? "text-danger" : iconClass}>
          {failed ? <AlertTriangle size={15} strokeWidth={1.8} aria-hidden /> : icon}
        </span>
      </div>
      <div
        className={`tnum mt-2 text-[26px] font-medium leading-none tracking-tight ${
          failed || value === null ? "text-muted" : ""
        }`}
      >
        {value ?? "—"}
      </div>
      <div className={`mt-2 truncate text-xs ${failed ? "text-danger" : "text-muted"}`} title={error ?? hint}>
        {failed ? `unreadable: ${error}` : hint}
      </div>
    </div>
  );
}

export function StatCardSkeleton() {
  return (
    <div className="rounded-xl border border-border bg-surface p-4" aria-busy="true">
      <div className="h-3 w-24 animate-pulse rounded bg-raised" />
      <div className="tnum mt-3 h-7 w-14 animate-pulse rounded bg-raised" />
      <div className="mt-3 h-3 w-32 animate-pulse rounded bg-raised" />
    </div>
  );
}