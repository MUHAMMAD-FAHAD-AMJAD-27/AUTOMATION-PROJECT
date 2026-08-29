"use client";

import { RefreshCw } from "lucide-react";

/**
 * The one interactive piece of PanelError, isolated in its own client module.
 *
 * PanelError previously carried an inline `onClick` while `panel.tsx` had no
 * "use client" directive, so it was a Server Component with an event handler —
 * which React refuses to serialize ("Event handlers cannot be passed to Client
 * Component props"). The component therefore crashed the moment it rendered,
 * i.e. exactly when a section had already failed, replacing a readable error
 * with a blown-up page. Keeping the boundary here means Panel / PanelEmpty /
 * PanelSkeleton stay server-rendered with no client JS.
 */
export function RetryButton() {
  return (
    <button
      type="button"
      onClick={() => window.location.reload()}
      className="inline-flex items-center gap-1.5 rounded-md border border-border bg-raised px-2.5 py-1.5 text-xs text-fg hover:bg-bg"
    >
      <RefreshCw size={12} strokeWidth={1.8} aria-hidden />
      Retry
    </button>
  );
}
