import type { ReactNode } from "react";
import { AlertTriangle, Inbox, RefreshCw } from "lucide-react";
import Link from "next/link";

export function Panel({
  title,
  action,
  children,
  className = "",
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border border-border bg-surface ${className}`}>
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-[13px] font-medium tracking-wide">{title}</h2>
        {action}
      </div>
      <div className="p-4">{children}</div>
    </section>
  );
}

export function PanelError({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="flex flex-col items-start gap-3 rounded-lg border border-danger/25 bg-danger/5 p-4 text-sm"
    >
      <div className="flex items-center gap-2 font-medium text-danger">
        <AlertTriangle size={15} strokeWidth={1.8} aria-hidden />
        Failed to load this section
      </div>
      <p className="text-muted">{message}</p>
      <a
        href="#"
        onClick={(e) => {
          e.preventDefault();
          window.location.reload();
        }}
        className="inline-flex items-center gap-1.5 rounded-md border border-border bg-raised px-2.5 py-1.5 text-xs text-fg hover:bg-bg"
      >
        <RefreshCw size={12} strokeWidth={1.8} aria-hidden />
        Retry
      </a>
    </div>
  );
}

export function PanelEmpty({
  title,
  description,
  href,
  hrefLabel,
}: {
  title: string;
  description: string;
  href?: string;
  hrefLabel?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      <Inbox size={22} strokeWidth={1.5} className="text-muted" aria-hidden />
      <p className="text-sm font-medium">{title}</p>
      <p className="max-w-sm text-xs text-muted">{description}</p>
      {href && hrefLabel && (
        <Link
          href={href}
          className="mt-2 rounded-md border border-border bg-raised px-3 py-1.5 text-xs text-fg transition-colors hover:bg-bg"
        >
          {hrefLabel}
        </Link>
      )}
    </div>
  );
}

export function PanelSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-10 animate-pulse rounded-md bg-raised" />
      ))}
    </div>
  );
}