/**
 * JS mirror of the design tokens in index.css — for libraries (Recharts,
 * canvas, inline SVG) that need literal colour strings.
 */
export const colors = {
  primary: "#2563EB",
  primarySoft: "#DBEAFE",
  info: "#0EA5E9",
  success: "#16A34A",
  warning: "#D97706",
  danger: "#DC2626",
  violet: "#7C3AED",
  text: "#0F172A",
  muted: "#64748B",
  grid: "#E2E8F0",
  surface: "#FFFFFF",
};

export const chartPalette = [colors.primary, colors.info, colors.success, colors.warning, colors.violet];

/** Shared Recharts styling so every chart looks the same. */
export const chartTheme = {
  grid: { stroke: colors.grid, strokeDasharray: "3 3", vertical: false },
  axis: { stroke: colors.muted, fontSize: 12, tickLine: false, axisLine: false },
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
    cursor: { fill: "rgba(37,99,235,0.06)" },
  },
  legend: { wrapperStyle: { fontSize: 12, color: colors.muted, paddingTop: 8 } },
};
