import React from "react";
import { LogIn as LogInIcon, LogOut, Clock, Camera } from "lucide-react";
import { formatTime, formatDurationShort } from "@/lib/time";
import { EmptyState } from "@/components/common/EmptyState";
import { cn } from "@/lib/utils";

/** Entry / exit timeline for one day's sessions (sorted ascending). */
export default function VisitTimeline({ sessions }) {
  if (!sessions || sessions.length === 0) {
    return <EmptyState compact icon={Clock} title="No visits in this range" />;
  }

  const points = [];
  sessions.forEach((s, i) => {
    points.push({ id: `${s.id}-in`, kind: "entry", time: s.entry_time, camera: s.entry_camera, idx: i });
    if (s.exit_time) points.push({ id: `${s.id}-out`, kind: "exit", time: s.exit_time, camera: s.exit_camera, idx: i, duration: s.duration_seconds });
    else points.push({ id: `${s.id}-live`, kind: "live", idx: i });
  });

  const STYLE = {
    entry: { icon: LogInIcon, dot: "bg-primary text-white", label: "Entry", text: "text-primary" },
    exit: { icon: LogOut, dot: "bg-info text-white", label: "Exit", text: "text-info" },
    live: { icon: Clock, dot: "bg-warning text-white animate-pulse", label: "Still inside", text: "text-warning" },
  };

  return (
    <ol className="relative space-y-3 pl-9" data-testid="visit-timeline">
      <span className="absolute bottom-3 left-[13px] top-3 w-px bg-border" />
      {points.map((p) => {
        const st = STYLE[p.kind];
        const Icon = st.icon;
        return (
          <li key={p.id} className="relative" data-testid={`timeline-point-${p.id}`}>
            <span className={cn("absolute -left-9 top-1/2 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-full ring-4 ring-card", st.dot)}>
              <Icon className="h-3.5 w-3.5" />
            </span>
            <div className="flex items-center justify-between gap-3 rounded-lg border bg-card px-4 py-2.5">
              <div className="flex items-center gap-3">
                <span className="w-24 text-sm font-semibold tabular-nums">{p.kind === "live" ? "—" : formatTime(p.time)}</span>
                <span className={cn("text-xs font-medium", st.text)}>Visit {p.idx + 1} · {st.label}</span>
              </div>
              <div className="hidden items-center gap-3 text-xs text-muted-foreground sm:flex">
                {p.camera && <span className="inline-flex items-center gap-1 font-mono"><Camera className="h-3 w-3" />{p.camera}</span>}
                {p.kind === "exit" && p.duration > 0 && (
                  <span className="rounded-full bg-muted px-2 py-0.5 font-medium text-foreground">{formatDurationShort(p.duration)}</span>
                )}
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
