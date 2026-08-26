import { CATEGORIES, getOffers } from "@/lib/queries";
import { OffersTable } from "@/components/offers-table";
import { Panel, PanelEmpty, PanelError } from "@/components/panel";
import { Search } from "lucide-react";

export const dynamic = "force-dynamic";

const VERIFY_OPTIONS = ["verified", "live", "unconfirmed", "expired", "dead", "reported"];

export default async function OffersPage({
  searchParams,
}: {
  searchParams: { [key: string]: string | string[] | undefined };
}) {
  const single = (v: string | string[] | undefined) => (typeof v === "string" ? v : undefined);
  const q = single(searchParams.q) ?? "";
  const category = single(searchParams.category) ?? "all";
  const status = single(searchParams.status) ?? "all";
  const availability = (single(searchParams.availability) ?? "active") as "active" | "expired" | "all";

  const rows = await getOffers({ q, category, status, availability, limit: 200 });

  return (
    <div className="flex flex-col gap-4" data-od-id="offers-feed">
      <form method="get" action="/offers" className="flex flex-wrap items-end gap-3">
        <div className="flex min-w-[220px] flex-1 flex-col gap-1">
          <label htmlFor="q" className="kbd-hint">Search</label>
          <div className="relative">
            <Search
              size={14}
              strokeWidth={1.8}
              className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted"
              aria-hidden
            />
            <input
              id="q"
              name="q"
              type="search"
              defaultValue={q}
              placeholder="e.g. credits, GPU, domain…"
              className="w-full rounded-lg border border-border bg-raised py-2 pr-3 pl-8 text-sm text-fg placeholder:text-muted/60 focus:border-accent/60"
            />
          </div>
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="category" className="kbd-hint">Category</label>
          <select
            id="category"
            name="category"
            defaultValue={category}
            className="rounded-lg border border-border bg-raised px-3 py-2 text-sm text-fg focus:border-accent/60"
          >
            <option value="all">All categories</option>
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="status" className="kbd-hint">Verification</label>
          <select
            id="status"
            name="status"
            defaultValue={status}
            className="rounded-lg border border-border bg-raised px-3 py-2 text-sm text-fg focus:border-accent/60"
          >
            <option value="all">Any status</option>
            {VERIFY_OPTIONS.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="availability" className="kbd-hint">Availability</label>
          <select
            id="availability"
            name="availability"
            defaultValue={availability}
            className="rounded-lg border border-border bg-raised px-3 py-2 text-sm text-fg focus:border-accent/60"
          >
            <option value="active">Active only</option>
            <option value="expired">Expired only</option>
            <option value="all">All</option>
          </select>
        </div>
        <button
          type="submit"
          className="rounded-lg border border-border bg-raised px-4 py-2 text-sm text-fg transition-colors hover:bg-bg"
        >
          Filter
        </button>
        {(q || category !== "all" || status !== "all" || availability !== "active") && (
          <a href="/offers" className="px-1 py-2 text-xs text-accent hover:underline">
            Reset
          </a>
        )}
      </form>

      <Panel title={`Results · ${rows.length}`}>
        {rows.length === 0 ? (
          <PanelEmpty
            title="No offers match these filters"
            description={
              q || category !== "all" || status !== "all"
                ? "Loosen the filters, or paste a new deal to grow the feed."
                : "No offers in the database yet — run the pipeline or paste your first deal."
            }
            href="/ingest"
            hrefLabel="Paste a deal"
          />
        ) : (
          <OffersTable rows={rows} />
        )}
      </Panel>
    </div>
  );
}