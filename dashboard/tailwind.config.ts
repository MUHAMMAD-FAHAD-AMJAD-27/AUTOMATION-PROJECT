import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Neutral layer (dark ops tool — no pure black/white, per craft color rules)
        bg: "#0B0E13",
        surface: "#12161D",
        raised: "#181E28",
        border: "rgba(255,255,255,0.08)",
        fg: "#E6E9EE",
        muted: "#98A1B2",
        // Single accent — warm bronze ("valuable find"); no default indigo/cyan
        accent: {
          DEFAULT: "#D8B46A",
          soft: "rgba(216,180,106,0.14)",
          faint: "rgba(216,180,106,0.06)",
        },
        success: "#4CC38A",
        warn: "#E8A33D",
        danger: "#E5534B",
        // Category palette = the pipeline's own dispatch taxonomy (data semantics)
        cat: {
          cloud: "#0EA5E9",
          llm: "#8B5CF6",
          hosting: "#F59E0B",
          domain: "#10B981",
          tools: "#6366F1",
          student: "#EC4899",
          course: "#14B8A6",
          coupon: "#EF4444",
          other: "#64748B",
        },
      },
      fontFamily: {
        sans: ["Segoe UI Variable", "Segoe UI", "system-ui", "sans-serif"],
        mono: ["Cascadia Code", "JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      letterSpacing: {
        caps: "0.08em",
      },
    },
  },
  plugins: [],
};

export default config;