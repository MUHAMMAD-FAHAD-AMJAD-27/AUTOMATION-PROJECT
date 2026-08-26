import { Suspense } from "react";
import { Tags } from "lucide-react";
import { LoginForm } from "./login-form";

export const dynamic = "force-dynamic";

export default function LoginPage() {
  return (
    <div className="flex min-h-[70vh] items-center justify-center" data-od-id="login">
      <div className="w-full max-w-sm rounded-xl border border-border bg-surface p-6">
        <div className="mb-5 flex items-center gap-2.5">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent-soft text-accent">
            <Tags size={17} strokeWidth={1.8} aria-hidden />
          </span>
          <div className="leading-tight">
            <div className="text-sm font-semibold">Freebies Ops</div>
            <div className="text-[11px] tracking-caps text-muted uppercase">sign-in required</div>
          </div>
        </div>
        <Suspense fallback={<div className="h-32 animate-pulse rounded-lg bg-raised" />}>
          <LoginForm />
        </Suspense>
        <p className="mt-4 text-xs leading-relaxed text-muted">
          Set <code className="font-mono">DASHBOARD_API_KEY</code> in your environment. The key is
          stored only as an httpOnly cookie — never sent to the browser bundle.
        </p>
      </div>
    </div>
  );
}