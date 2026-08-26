"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { CheckCircle2, Loader2, Send } from "lucide-react";

type Errors = { text?: string; urls?: string; form?: string };

const EMPTY: Errors = {};

function parseUrls(raw: string): string[] {
  return raw
    .split(/[\s,]+/)
    .map((u) => u.trim())
    .filter(Boolean)
    .slice(0, 8);
}

export function IngestForm() {
  const router = useRouter();
  const [text, setText] = useState("");
  const [urlsRaw, setUrlsRaw] = useState("");
  const [author, setAuthor] = useState("");
  const [errors, setErrors] = useState<Errors>(EMPTY);
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [submitting, setSubmitting] = useState(false);
  const [submittedId, setSubmittedId] = useState<number | null>(null);

  // Validate on blur, not per keystroke (form-validation craft)
  function validate(): Errors {
    const next: Errors = {};
    if (text.trim().length < 10) {
      next.text = "Paste at least 10 characters of the deal description.";
    }
    const urls = parseUrls(urlsRaw);
    const badUrl = urls.find((u) => !/^https?:\/\/\S+$/i.test(u));
    if (badUrl) {
      next.urls = `"${badUrl}" is not a valid http(s) URL.`;
    }
    return next;
  }

  function handleBlur(field: keyof Errors) {
    setTouched((t) => ({ ...t, [field]: true }));
    const next = validate();
    if (next[field]) setErrors((e) => ({ ...e, [field]: next[field] }));
    else setErrors((e) => ({ ...e, [field]: undefined }));
  }

  function handleChange(field: "text" | "urls" | "author", value: string) {
    if (field === "text") setText(value);
    if (field === "urls") setUrlsRaw(value);
    if (field === "author") setAuthor(value);
    // Clear the field's error as soon as the user edits it
    setErrors((e) => ({ ...e, [field]: undefined }));
  }

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const next = validate();
    setTouched({ text: true, urls: true });
    if (next.text || next.urls) {
      setErrors(next);
      // Focus the first invalid field
      const first = next.text ? "deal-text" : "deal-urls";
      document.getElementById(first)?.focus();
      return;
    }

    setSubmitting(true);
    setErrors(EMPTY);
    try {
      const res = await fetch("/api/ingest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: text.trim(),
          urls: parseUrls(urlsRaw),
          authorHandle: author.trim() || undefined,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setErrors({ form: body.error || "The server rejected the deal." });
        return;
      }
      setSubmittedId(body.id as number);
      setText("");
      setUrlsRaw("");
      setAuthor("");
    } catch {
      setErrors({ form: "Network error — could not reach the API." });
    } finally {
      setSubmitting(false);
    }
  }

  if (submittedId !== null) {
    return (
      <div
        role="status"
        className="flex flex-col items-start gap-3 rounded-xl border border-success/25 bg-success/5 p-6"
      >
        <div className="flex items-center gap-2 font-medium text-success">
          <CheckCircle2 size={17} strokeWidth={1.8} aria-hidden />
          Deal queued into <span className="font-mono text-xs">raw_items</span>
        </div>
        <p className="text-sm text-muted">
          Row id <span className="tnum">#{submittedId}</span>. The next pipeline run will
          normalize, verify, and dispatch it automatically — no further action needed here.
        </p>
        <button
          type="button"
          onClick={() => setSubmittedId(null)}
          className="rounded-md border border-border bg-raised px-3 py-1.5 text-xs text-fg transition-colors hover:bg-bg"
        >
          Paste another deal
        </button>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-5">
      {errors.form && (
        <div
          role="alert"
          tabIndex={-1}
          className="rounded-lg border border-danger/25 bg-danger/5 px-3 py-2.5 text-sm text-danger"
        >
          {errors.form}
        </div>
      )}

      <div className="flex flex-col gap-1.5">
        <label htmlFor="deal-text" className="text-[13px] font-medium">
          Deal description <span className="text-danger" aria-hidden>*</span>
        </label>
        <textarea
          id="deal-text"
          name="text"
          rows={6}
          required
          value={text}
          onChange={(e) => handleChange("text", e.target.value)}
          onBlur={() => handleBlur("text")}
          aria-invalid={Boolean(touched.text && errors.text)}
          aria-describedby={errors.text ? "deal-text-error" : undefined}
          placeholder="e.g. 3 months free of X Cloud — $300 credit for new dev accounts, expires end of month. Apply at https://…"
          className="resize-y rounded-lg border border-border bg-raised px-3 py-2.5 text-sm text-fg placeholder:text-muted/60 focus:border-accent/60"
        />
        {errors.text && touched.text && (
          <p id="deal-text-error" role="alert" className="text-xs text-danger">
            {errors.text}
          </p>
        )}
      </div>

      <div className="flex flex-col gap-1.5">
        <label htmlFor="deal-urls" className="text-[13px] font-medium">
          Source URL(s) <span className="text-muted">— optional, comma or space separated</span>
        </label>
        <input
          id="deal-urls"
          name="urls"
          type="text"
          value={urlsRaw}
          onChange={(e) => handleChange("urls", e.target.value)}
          onBlur={() => handleBlur("urls")}
          aria-invalid={Boolean(touched.urls && errors.urls)}
          aria-describedby={errors.urls ? "deal-urls-error" : undefined}
          placeholder="https://facebook.com/groups/… https://t.me/…"
          className="rounded-lg border border-border bg-raised px-3 py-2.5 text-sm text-fg placeholder:text-muted/60 focus:border-accent/60"
        />
        {errors.urls && touched.urls && (
          <p id="deal-urls-error" role="alert" className="text-xs text-danger">
            {errors.urls}
          </p>
        )}
      </div>

      <div className="flex flex-col gap-1.5">
        <label htmlFor="deal-author" className="text-[13px] font-medium">
          Author handle <span className="text-muted">— optional</span>
        </label>
        <input
          id="deal-author"
          name="author"
          type="text"
          value={author}
          onChange={(e) => handleChange("author", e.target.value)}
          placeholder="e.g. somefacebookgroup (defaults to manual:paste)"
          className="rounded-lg border border-border bg-raised px-3 py-2.5 text-sm text-fg placeholder:text-muted/60 focus:border-accent/60"
        />
      </div>

      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={submitting}
          className="inline-flex items-center gap-2 rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-bg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? (
            <Loader2 size={15} className="animate-spin" aria-hidden />
          ) : (
            <Send size={15} strokeWidth={1.8} aria-hidden />
          )}
          {submitting ? "Queuing…" : "Queue for pipeline"}
        </button>
        <span className="text-xs text-muted">
          Pasted finds join the same queue as Telegram / Twitter items.
        </span>
      </div>
    </form>
  );
}