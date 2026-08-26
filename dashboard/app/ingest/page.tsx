import { IngestForm } from "@/components/ingest-form";
import { Panel } from "@/components/panel";

export const dynamic = "force-dynamic";

const FLOW_STEPS = [
  { n: 1, label: "Insert", text: "POST /api/ingest writes a row into raw_items under the manual:dashboard source." },
  { n: 2, label: "Normalize", text: "The pipeline pulls it, cleans the title/URL and classifies a category." },
  { n: 3, label: "Verify", text: "Liveness probe (dead links never pay LLM tokens), then LLM extraction." },
  { n: 4, label: "Dispatch", text: "Verified offers are posted to your Discord webhook, deduped by URL hash + similarity." },
];

export default function IngestPage() {
  return (
    <div className="grid gap-4 lg:grid-cols-5" data-od-id="manual-ingest">
      <div className="lg:col-span-3">
        <Panel title="Paste a manual find">
          <p className="mb-5 text-sm text-muted">
            Found something in a Facebook group, a newsletter, or a forum? Paste it here — it enters the
            exact same queue as Telegram / Twitter items and flows through the full pipeline.
          </p>
          <IngestForm />
        </Panel>
      </div>
      <div className="lg:col-span-2">
        <Panel title="What happens next">
          <ol className="flex flex-col gap-4">
            {FLOW_STEPS.map((step) => (
              <li key={step.n} className="flex gap-3">
                <span className="tnum flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-border bg-raised text-xs text-accent">
                  {step.n}
                </span>
                <div>
                  <div className="text-[13px] font-medium">{step.label}</div>
                  <p className="mt-0.5 text-xs leading-relaxed text-muted">{step.text}</p>
                </div>
              </li>
            ))}
          </ol>
          <div className="mt-5 rounded-lg border border-border bg-raised p-3">
            <div className="kbd-hint">Trigger the run</div>
            <code className="mt-1 block font-mono text-xs text-fg">
              python run.py pipeline --source manual:dashboard
            </code>
          </div>
        </Panel>
      </div>
    </div>
  );
}