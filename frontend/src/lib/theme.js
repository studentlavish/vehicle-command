/**
 * JS mirror of the design tokens in index.css — for libraries (Recharts,
 * inline SVG) that need colour strings. Neutral colours point at the CSS
 * variables so charts follow the light / dark theme automatically; brand and
 * status colours are literal (they read well on both backgrounds).
 */
export const colors = {
  primary: "#2563EB",
  primarySoft: "#DBEAFE",
  info: "#0EA5E9",
  success: "#16A34A",
  warning: "#D97706",
  danger: "#DC2626",
  violet: "#7C3AED",
  text: "hsl(var(--foreground))",
  muted: "hsl(var(--muted-foreground))",
  grid: "hsl(var(--border))",
  surface: "hsl(var(--popover))",
};

export const chartPalette = [colors.primary, colors.info, colors.success, colors.warning, colors.violet];

/** Shared Recharts styling so every chart looks the same. */
export const chartTheme = {
  grid: { stroke: colors.grid, strokeDasharray: "3 3", vertical: false },
  axis: { stroke: colors.muted, fontSize: 12, tickLine: false, axisLine: false, tick: { fill: colors.muted } },
  tooltip: {
    contentStyle: {
      background: colors.surface,
      border: `1px solid ${colors.grid}`,
      borderRadius: 10,
      boxShadow: "0 8px 24px -8px rgba(15,23,42,0.18)",
      color: colors.text,
      fontSize: 12,
    },
    labelStyle: { color: colors.muted, fontWeight: 500, marginBottom: 4 },
    itemStyle: { color: colors.text },
    cursor: { fill: "hsl(var(--primary) / 0.06)" },
  },
  legend: { wrapperStyle: { fontSize: 12, color: colors.muted, paddingTop: 8 } },
};
