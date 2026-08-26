import { getSources } from "@/lib/queries";
import { Panel, PanelEmpty, PanelError } from "@/components/panel";
import { CursorsTable, DiscoveredSummary, SourcesTable } from "@/components/sources-table";
import { StatusBadge } from "@/components/badges";
import { timeAgo } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function SourcesPage() {
  const data = await getSources();

  return (
    <div className="flex flex-col gap-4" data-od-id="sources-monitor">
      <Panel title="Adapters" action={<span className="text-xs text-muted">{data.sources.length} configured</span>}>
        {data.sources.length === 0 ? (
          <PanelEmpty
            title="No sources configured"
            description="Sources are seeded by the pipeline adapters on first run."
          />
        ) : (
          <SourcesTable sources={data.sources} />
        )}
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Sync watermarks">
          <CursorsTable cursors={data.cursors} />
        </Panel>
        <Panel title="Discovered channels">
          <DiscoveredSummary discovered={data.discovered} />
        </Panel>
      </div>

      <Panel title="Recent pipeline runs">
        {data.recentRuns.length === 0 ? (
          <PanelEmpty
            title="No runs recorded"
            description="Run: python run.py pipeline — each invocation writes a row here."
          />
        ) : (
          <ul className="flex flex-col divide-y divide-border/60">
            {data.recentRuns.map((run, i) => (
              <li key={i} className="flex items-center justify-between py-2.5">
                <span className="font-mono text-xs text-muted">{run.flow_key}</span>
                <span className="flex items-center gap-3">
                  <span className="tnum text-xs text-muted">{timeAgo(run.started_at)}</span>
                  <StatusBadge status={run.status} />
                </span>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}