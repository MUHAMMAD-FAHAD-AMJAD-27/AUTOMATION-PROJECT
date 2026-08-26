"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Tags,
  RadioTower,
  ClipboardPlus,
  LogOut,
} from "lucide-react";

const NAV = [
  {
    group: "Monitor",
    items: [
      { href: "/", label: "Overview", icon: LayoutDashboard, exact: true },
      { href: "/offers", label: "Offers", icon: Tags },
      { href: "/sources", label: "Sources", icon: RadioTower },
    ],
  },
  {
    group: "Act",
    items: [{ href: "/ingest", label: "Ingest deal", icon: ClipboardPlus }],
  },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="sticky top-0 hidden h-screen flex-col border-r border-border bg-surface md:flex">
      <div className="flex items-center gap-2.5 px-5 pt-5 pb-4">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent-soft text-accent">
          <Tags size={16} strokeWidth={1.8} aria-hidden />
        </span>
        <div className="leading-tight">
          <div className="text-sm font-semibold tracking-wide">Freebies Ops</div>
          <div className="text-[11px] tracking-caps text-muted uppercase">pipeline console</div>
        </div>
      </div>

      <nav aria-label="Primary" className="flex-1 overflow-y-auto px-3 py-2">
        {NAV.map((section) => (
          <div key={section.group} className="mb-4">
            <div className="px-2 pb-1.5 text-[10px] tracking-caps text-muted uppercase">
              {section.group}
            </div>
            <ul className="flex flex-col gap-0.5">
              {section.items.map((item) => {
                const active = item.exact
                  ? pathname === item.href
                  : pathname.startsWith(item.href);
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      aria-current={active ? "page" : undefined}
                      className={`flex items-center gap-2.5 rounded-md px-2.5 py-2 text-[13px] transition-colors ${
                        active
                          ? "bg-accent-faint font-medium text-accent"
                          : "text-fg/80 hover:bg-raised hover:text-fg"
                      }`}
                    >
                      <item.icon size={15} strokeWidth={1.8} aria-hidden />
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      <div className="border-t border-border p-3">
        <a
          href="/api/logout"
          className="flex items-center gap-2.5 rounded-md px-2.5 py-2 text-[13px] text-muted transition-colors hover:bg-raised hover:text-fg"
        >
          <LogOut size={15} strokeWidth={1.8} aria-hidden />
          Sign out
        </a>
      </div>
    </aside>
  );
}