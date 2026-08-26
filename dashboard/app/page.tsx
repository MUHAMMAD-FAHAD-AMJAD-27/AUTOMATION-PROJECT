import { Suspense } from "react";
import Link from "next/link";
import { Activity, Inbox, Send, Tags } from "lucide-react";
import { getOverviewStats } from "@/lib/queries";
import { StatCard, StatCardSkeleton } from "@/components/stat-card";
import { Panel, PanelEmpty, PanelError, PanelSkeleton } from "@/components/panel";
import { StatusBadge } from "@/components/badges";
import { timeAgo } from "@/lib/format";

export const dynamic = "force-dynamic";

function CategoryList({
  items,
}: {
  items: { category: string; count: number }[];
}) {
  const max = Math.max(1, ...items.map((i) => i.count));
  return (
    <ul className="flex flex-col gap-3">
      {items.map((item) => (
        <li key={item.category} className="flex items-center gap-3">
          <span className="w-20 shrink-0 text-xs capitalize text-muted">{item.category}</span>
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-raised">
            <div
              className="h-full rounded-full bg-accent/70"
              style={{ width: `${(item.count / max) * 100}%` }}
            />
          </div>
          <span className="tnum w-10 shrink-0 text-right text-xs text-fg">{item.count}</span>
        </li>
      ))}
    </ul>
  );
}

function WeekBars({ days }: { days: { day: string; count: number }[] }) {
  const max = Math.max(1, ...days.map((d) => d.count));
  const labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  return (
    <div className="flex h-40 items-end gap-2" role="img" aria-label="Offers discovered, last 7 days">
      {days.map((d, i) => (
        <div key={d.day} className="flex flex-1 flex-col items-center gap-1.5">
          <span className="tnum text-[10px] text-muted">{d.count || ""}</span>
          <div
            className={`w-full rounded-t ${d.count > 0 ? "bg-accent/80" : "bg-raised"}`}
            style={{ height: `${Math.max(3, (d.count / max) * 100)}%` }}
          />
          <span className="text-[10px] text-muted">{labels[(new Date(d.day).getDay() + 6) % 7]}</span>
        </div>
      ))}
    </div>
  );
}

function runStatsSummary(stats: unknown): string {
  if (!stats || typeof stats !== "object") return "no stats recorded";
  const s = stats as Record<string, unknown>;
  const interesting = ["fetched", "normalized", "verified", "dispatched", "skipped", "failed"];
  const parts = interesting.filter((k) => typeof s[k] === "number").map((k) => `${k}=${s[k]}`);
  return parts.length ? parts.join(" · ") : "no stats recorded";
}

export default async function OverviewPage() {
  const stats = await getOverviewStats();

  const kpis = (
    <div data-od-id="kpi-row" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <StatCard
        label="Active offers"
        value={String(stats.activeOffers)}
        hint="verified or live, not expired"
        icon={<Tags size={15} strokeWidth={1.8} aria-hidden />}
        iconClass="text-accent"
      />
      <StatCard
        label="Pending queue"
        value={String(stats.pendingQueue)}
        hint="raw items awaiting next run"
        icon={<Inbox size={15} strokeWidth={1.8} aria-hidden />}
        iconClass="text-warn"
      />
      <StatCard
        label="Total dispatches"
        value={String(stats.totalDispatches)}
        hint="sent to Discord channels"
        icon={<Send size={15} strokeWidth={1.8} aria-hidden />}
        iconClass="text-success"
      />
      <StatCard
        label="Last pipeline run"
        value={stats.lastRun ? stats.lastRun.status : "never"}
        hint={stats.lastRun?.startedAt ? `${timeAgo(stats.lastRun.startedAt)} · ${runStatsSummary(stats.lastRun.stats)}` : "run ./run.py pipeline to start"}
        icon={<Activity size={15} strokeWidth={1.8} aria-hidden />}
        iconClass={stats.lastRun?.status === "success" ? "text-success" : "text-muted"}
      />
    </div>
  );

  return (
    <div className="flex flex-col gap-6" data-od-id="overview">
      {kpis}

      <div data-od-id="charts-row" className="grid gap-4 lg:grid-cols-5">
        <Panel title="Offers discovered · last 7 days" className="lg:col-span-3">
          <Suspense fallback={<PanelSkeleton rows={4} />}>
            {stats.last7Days.length === 0 ? (
              <PanelEmpty
                title="No offers recorded yet"
                description="Once the pipeline processes raw items, discovery history will appear here."
              />
            ) : (
              <WeekBars days={stats.last7Days} />
            )}
          </Suspense>
        </Panel>

        <Panel title="Active by category" className="lg:col-span-2">
          <Suspense fallback={<PanelSkeleton rows={5} />}>
            {stats.categoryCounts.length === 0 ? (
              <PanelEmpty
                title="No active offers"
                description="Categories populate as offers are verified."
                href="/ingest"
                hrefLabel="Paste a deal"
              />
            ) : (
              <CategoryList items={stats.categoryCounts} />
            )}
          </Suspense>
        </Panel>
      </div>

      <Panel
        title="Recent pipeline run"
        action={
          <Link
            href="/sources"
            className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
          >
            View source history
          </Link>
        }
      >
        {stats.lastRun ? (
          <dl className="grid gap-3 sm:grid-cols-4">
            <div>
              <dt className="kbd-hint">Status</dt>
              <dd className="mt-1">
                <StatusBadge status={stats.lastRun.status} />
              </dd>
            </div>
            <div>
              <dt className="kbd-hint">Started</dt>
              <dd className="tnum mt-1 text-sm">{timeAgo(stats.lastRun.startedAt)}</dd>
            </div>
            <div>
              <dt className="kbd-hint">Finished</dt>
              <dd className="tnum mt-1 text-sm">
                {stats.lastRun.finishedAt ? timeAgo(stats.lastRun.finishedAt) : "—"}
              </dd>
            </div>
            <div>
              <dt className="kbd-hint">Summary</dt>
              <dd className="mt-1 truncate font-mono text-xs text-muted">
                {runStatsSummary(stats.lastRun.stats)}
              </dd>
            </div>
          </dl>
        ) : (
          <PanelEmpty
            title="No pipeline run yet"
            description="Pipeline runs are recorded in the runs table. Start one with: python run.py pipeline"
          />
        )}
      </Panel>
    </div>
  );
}