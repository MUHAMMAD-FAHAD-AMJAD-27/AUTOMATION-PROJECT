"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, Tags, RadioTower, ClipboardPlus } from "lucide-react";

const MOBILE_NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard, exact: true },
  { href: "/offers", label: "Offers", icon: Tags },
  { href: "/sources", label: "Sources", icon: RadioTower },
  { href: "/ingest", label: "Ingest", icon: ClipboardPlus },
];

const TITLES: { match: (p: string) => boolean; title: string; sub: string }[] = [
  { match: (p) => p === "/", title: "Overview", sub: "Pipeline health at a glance" },
  { match: (p) => p.startsWith("/offers"), title: "Offers", sub: "Browse and filter captured deals" },
  { match: (p) => p.startsWith("/sources"), title: "Sources", sub: "Adapter health and sync watermarks" },
  { match: (p) => p.startsWith("/ingest"), title: "Ingest deal", sub: "Paste a manual find into the pipeline" },
  { match: (p) => p.startsWith("/login"), title: "Sign in", sub: "Single operator gate" },
];

export function Topbar() {
  const pathname = usePathname();
  const current = TITLES.find((t) => t.match(pathname)) ?? TITLES[0];

  return (
    <header className="sticky top-0 z-20 border-b border-border bg-bg/90 backdrop-blur">
      <div className="flex items-center justify-between px-4 py-3 sm:px-6 md:px-8">
        <div>
          <h1 className="text-[17px] font-medium tracking-tight">{current.title}</h1>
          <p className="hidden text-xs text-muted sm:block">{current.sub}</p>
        </div>
        <div className="flex items-center gap-1 md:hidden" role="navigation" aria-label="Secondary">
          {MOBILE_NAV.map((item) => {
            const active = item.exact ? pathname === item.href : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-label={item.label}
                aria-current={active ? "page" : undefined}
                className={`rounded-md p-2 transition-colors ${
                  active ? "bg-accent-faint text-accent" : "text-muted hover:bg-raised hover:text-fg"
                }`}
              >
                <item.icon size={17} strokeWidth={1.8} aria-hidden />
              </Link>
            );
          })}
        </div>
      </div>
    </header>
  );
}