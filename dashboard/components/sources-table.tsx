import type { CursorRow, DiscoveredRow, SourceRow } from "@/lib/queries";
import { SourceKindBadge } from "./badges";
import { timeAgo } from "@/lib/format";

export function SourcesTable({ sources }: { sources: SourceRow[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[680px] border-collapse text-left text-[13px]">
        <thead>
          <tr className="border-b border-border text-[10px] tracking-caps text-muted uppercase">
            <th scope="col" className="px-3 py-2.5 font-medium">Source</th>
            <th scope="col" className="px-3 py-2.5 font-medium">Kind</th>
            <th scope="col" className="px-3 py-2.5 font-medium">Enabled</th>
            <th scope="col" className="px-3 py-2.5 font-medium">Health</th>
            <th scope="col" className="px-3 py-2.5 text-right font-medium">Updated</th>
          </tr>
        </thead>
        <tbody>
          {sources.map((row) => (
            <tr key={row.id} className="border-b border-border/60 transition-colors hover:bg-raised/60">
              <td className="px-3 py-2.5 font-medium">{row.name}</td>
              <td className="px-3 py-2.5"><SourceKindBadge kind={row.kind} /></td>
              <td className="px-3 py-2.5">
                {row.enabled ? (
                  <span className="text-success">enabled</span>
                ) : (
                  <span className="text-muted">paused</span>
                )}
              </td>
              <td className="max-w-[260px] truncate px-3 py-2.5 font-mono text-xs text-muted">
                {row.health ? JSON.stringify(row.health) : "—"}
              </td>
              <td className="tnum whitespace-nowrap px-3 py-2.5 text-right text-xs text-muted">
                {timeAgo(row.updated_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function CursorsTable({ cursors }: { cursors: CursorRow[] }) {
  if (cursors.length === 0) {
    return (
      <p className="py-6 text-center text-xs text-muted">
        No sync watermarks yet — a successful adapter run writes the first cursor.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px] border-collapse text-left text-[13px]">
        <thead>
          <tr className="border-b border-border text-[10px] tracking-caps text-muted uppercase">
            <th scope="col" className="px-3 py-2.5 font-medium">Channel</th>
            <th scope="col" className="px-3 py-2.5 font-medium">Source</th>
            <th scope="col" className="px-3 py-2.5 text-right font-medium">Last msg id</th>
            <th scope="col" className="px-3 py-2.5 text-right font-medium">Watermark</th>
          </tr>
        </thead>
        <tbody>
          {cursors.map((row, i) => (
            <tr key={i} className="border-b border-border/60 transition-colors hover:bg-raised/60">
              <td className="font-mono px-3 py-2.5 text-xs">{row.channel_username}</td>
              <td className="px-3 py-2.5 text-muted">{row.source_name}</td>
              <td className="tnum px-3 py-2.5 text-right text-xs text-muted">{row.last_message_id}</td>
              <td className="tnum px-3 py-2.5 text-right text-xs text-muted">{timeAgo(row.updated_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function DiscoveredSummary({ discovered }: { discovered: DiscoveredRow[] }) {
  if (discovered.length === 0) {
    return <p className="py-4 text-center text-xs text-muted">No channels discovered yet.</p>;
  }
  return (
    <div className="flex flex-wrap gap-2">
      {discovered.map((row) => (
        <span
          key={row.status}
          className="inline-flex items-center gap-1.5 rounded-full border border-border bg-raised px-2.5 py-1 text-xs"
        >
          <span className="tnum">{row.count}</span>
          <span className="text-muted">{row.status}</span>
        </span>
      ))}
    </div>
  );
}