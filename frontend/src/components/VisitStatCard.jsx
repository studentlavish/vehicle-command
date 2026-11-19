import React from "react";
import { cn } from "@/lib/utils";

const TONES = {
  blue: "bg-primary/10 text-primary",
  sky: "bg-info/10 text-info",
  emerald: "bg-success/10 text-success",
  violet: "bg-violet-50 text-violet-600",
  amber: "bg-warning/10 text-warning",
};

export default function VisitStatCard({ label, value, sub, icon: Icon, tone = "blue", testId }) {
  return (
    <div className="surface p-4" data-testid={testId}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-medium text-muted-foreground">{label}</div>
          <div className="mt-1.5 text-2xl font-semibold leading-tight tabular-nums">{value}</div>
          {sub ? <div className="mt-1 text-xs text-muted-foreground">{sub}</div> : null}
        </div>
        <div className={cn("flex h-9 w-9 shrink-0 items-center justify-center rounded-lg", TONES[tone] || TONES.blue)}>
          {Icon ? <Icon className="h-[18px] w-[18px]" /> : null}
        </div>
      </div>
    </div>
  );
}
