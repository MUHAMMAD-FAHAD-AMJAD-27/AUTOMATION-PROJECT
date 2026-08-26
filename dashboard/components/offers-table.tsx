import type { OfferRow } from "@/lib/queries";
import { CategoryBadge, StatusBadge } from "./badges";
import { expiresIn, formatValue, timeAgo, truncate } from "@/lib/format";
import { ExternalLink } from "lucide-react";

export function OffersTable({ rows }: { rows: OfferRow[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[840px] border-collapse text-left text-[13px]">
        <thead>
          <tr className="border-b border-border text-[10px] tracking-caps text-muted uppercase">
            <th scope="col" className="px-3 py-2.5 font-medium">Deal</th>
            <th scope="col" className="px-3 py-2.5 font-medium">Category</th>
            <th scope="col" className="px-3 py-2.5 font-medium">Value</th>
            <th scope="col" className="px-3 py-2.5 font-medium">Expires</th>
            <th scope="col" className="px-3 py-2.5 font-medium">Status</th>
            <th scope="col" className="px-3 py-2.5 font-medium">Source</th>
            <th scope="col" className="px-3 py-2.5 text-right font-medium">Seen</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const expiry = expiresIn(row.expires_at);
            return (
              <tr
                key={row.id}
                className="group border-b border-border/60 transition-colors hover:bg-raised/60"
              >
                <td className="max-w-[340px] px-3 py-2.5">
                  <a
                    href={row.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-1.5 font-medium text-fg hover:text-accent"
                  >
                    <span className="truncate">{truncate(row.title, 90)}</span>
                    <ExternalLink
                      size={12}
                      strokeWidth={1.8}
                      className="shrink-0 text-muted opacity-0 transition-opacity group-hover:opacity-100"
                      aria-hidden
                    />
                  </a>
                </td>
                <td className="px-3 py-2.5">
                  <CategoryBadge category={row.category} />
                </td>
                <td className="tnum px-3 py-2.5 text-muted">
                  {formatValue(row.value, row.currency)}
                </td>
                <td
                  className={`tnum px-3 py-2.5 ${
                    expiry.state === "expired"
                      ? "text-danger"
                      : expiry.state === "soon"
                        ? "text-warn"
                        : "text-muted"
                  }`}
                >
                  {expiry.label}
                </td>
                <td className="px-3 py-2.5">
                  <StatusBadge status={row.verification_status} />
                </td>
                <td className="max-w-[160px] truncate px-3 py-2.5 text-xs text-muted">
                  {row.source_name ?? "—"}
                </td>
                <td className="tnum whitespace-nowrap px-3 py-2.5 text-right text-xs text-muted">
                  {timeAgo(row.first_seen)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}