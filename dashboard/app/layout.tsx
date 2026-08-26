import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";
import { Sidebar } from "@/components/sidebar";
import { Topbar } from "@/components/topbar";

export const metadata: Metadata = {
  title: "Freebies Ops",
  description: "Developer freebies aggregation — pipeline operations dashboard",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-bg font-sans text-fg antialiased">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:m-3 focus:rounded focus:bg-raised focus:px-3 focus:py-2"
        >
          Skip to content
        </a>
        <div className="min-h-screen md:grid md:grid-cols-[220px_1fr]">
          <Sidebar />
          <div className="flex min-h-screen flex-col">
            <Topbar />
            <main id="main" className="flex-1 px-4 py-6 sm:px-6 md:px-8">
              {children}
            </main>
          </div>
        </div>
      </body>
    </html>
  );
}