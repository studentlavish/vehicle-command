import React from "react";
import { ResponsiveContainer, AreaChart, Area, YAxis } from "recharts";
import { ArrowUpRight, ArrowDownRight, Minus } from "lucide-react";
import { colors } from "@/lib/theme";
import { cn } from "@/lib/utils";

const TONES = {
  blue: { tile: "bg-primary/10 text-primary", stroke: colors.primary },
  sky: { tile: "bg-info/10 text-info", stroke: colors.info },
  green: { tile: "bg-success/10 text-success", stroke: colors.success },
  amber: { tile: "bg-warning/10 text-warning", stroke: colors.warning },
  violet: { tile: "bg-violet-50 text-violet-600", stroke: colors.violet },
};

export default function StatCard({ label, value, change, trend, icon: Icon, accent = "blue", testId, compareLabel = "vs yesterday" }) {
  const tone = TONES[accent] || TONES.blue;
  const hasChange = typeof change === "number" && !Number.isNaN(change);
  const up = hasChange && change > 0;
  const flat = hasChange && change === 0;
  const TrendIcon = flat ? Minus : up ? ArrowUpRight : ArrowDownRight;
  return (
    <div className="surface surface-hover flex flex-col p-5" data-testid={testId}>
      <div className="flex items-start justify-between gap-3">
        <div className="text-sm font-medium text-muted-foreground">{label}</div>
        <div className={cn("flex h-9 w-9 shrink-0 items-center justify-center rounded-lg", tone.tile)}>
          {Icon ? <Icon className="h-[18px] w-[18px]" /> : null}
        </div>
      </div>
      <div className="mt-1 text-[28px] font-semibold leading-none tracking-tight tabular-nums">{value ?? "—"}</div>
      <div className="mt-3 flex items-end justify-between gap-2">
        {hasChange ? (
          <div className="flex items-center gap-1.5 text-xs">
            <span
              className={cn(
                "inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 font-medium",
                flat ? "bg-muted text-muted-foreground" : up ? "bg-success/10 text-success" : "bg-destructive/10 text-destructive",
              )}
            >
              <TrendIcon className="h-3 w-3" />
              {Math.abs(change)}%
            </span>
            <span className="text-muted-foreground">{compareLabel}</span>
          </div>
        ) : <span />}
        {trend && trend.length > 1 ? (
          <div className="h-8 w-20 shrink-0">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={trend} margin={{ top: 2, bottom: 2, left: 0, right: 0 }}>
                <defs>
                  <linearGradient id={`grad-${testId}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={tone.stroke} stopOpacity={0.25} />
                    <stop offset="100%" stopColor={tone.stroke} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <YAxis hide domain={["dataMin", "dataMax + 1"]} />
                <Area type="monotone" dataKey="value" stroke={tone.stroke} strokeWidth={1.75} fill={`url(#grad-${testId})`} isAnimationActive={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        ) : null}
      </div>
    </div>
  );
}
