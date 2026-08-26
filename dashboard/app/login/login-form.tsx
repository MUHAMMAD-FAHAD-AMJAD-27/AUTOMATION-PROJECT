"use client";

import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { KeyRound, Loader2 } from "lucide-react";

export function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next") || "/";

  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!key.trim()) {
      setError("Enter the dashboard API key.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key }),
      });
      if (!res.ok) {
        setError("Incorrect key.");
        setBusy(false);
        return;
      }
      router.push(next);
      router.refresh();
    } catch {
      setError("Network error — try again.");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <label htmlFor="key" className="text-[13px] font-medium">
        Dashboard API key
      </label>
      <input
        id="key"
        name="key"
        type="password"
        autoFocus
        value={key}
        onChange={(e) => {
          setKey(e.target.value);
          if (error) setError(null);
        }}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? "login-error" : undefined}
        placeholder="DASHBOARD_API_KEY"
        className="rounded-lg border border-border bg-raised px-3 py-2.5 font-mono text-sm text-fg placeholder:text-muted/60 focus:border-accent/60"
      />
      {error && (
        <p id="login-error" role="alert" className="text-xs text-danger">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={busy}
        className="inline-flex items-center justify-center gap-2 rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-bg transition-opacity hover:opacity-90 disabled:opacity-50"
      >
        {busy ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <KeyRound size={15} strokeWidth={1.8} aria-hidden />}
        {busy ? "Checking…" : "Sign in"}
      </button>
    </form>
  );
}