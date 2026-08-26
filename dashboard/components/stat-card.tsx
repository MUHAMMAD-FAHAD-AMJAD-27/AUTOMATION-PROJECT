import type { ReactNode } from "react";

export function StatCard({
  label,
  value,
  hint,
  icon,
  iconClass = "text-muted",
}: {
  label: string;
  value: string;
  hint: string;
  icon: ReactNode;
  iconClass?: string;
}) {
  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <div className="flex items-center justify-between">
        <span className="text-[11px] tracking-caps text-muted uppercase">{label}</span>
        <span className={iconClass}>{icon}</span>
      </div>
      <div className="tnum mt-2 text-[26px] font-medium leading-none tracking-tight">{value}</div>
      <div className="mt-2 truncate text-xs text-muted">{hint}</div>
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