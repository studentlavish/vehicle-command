import React from "react";
import { motion } from "framer-motion";
import { LogIn as LogInIcon, LogOut, Clock } from "lucide-react";
import { formatTime, formatDurationShort } from "@/lib/time";

/**
 * Alternating Entry/Exit timeline for a single day (or session list).
 * Sessions must be sorted chronologically ascending.
 */
export default function VisitTimeline({ sessions }) {
  if (!sessions || sessions.length === 0) {
    return (
      <div className="text-center text-slate-400 text-sm py-10">
        No visit sessions in this range yet.
      </div>
    );
  }

  const points = [];
  sessions.forEach((s, i) => {
    points.push({ id: `${s.id}-in`, kind: "entry", time: s.entry_time, camera: s.entry_camera, sessionIdx: i });
    if (s.exit_time) {
      points.push({ id: `${s.id}-out`, kind: "exit", time: s.exit_time, camera: s.exit_camera, sessionIdx: i, duration: s.duration_seconds });
    } else {
      points.push({ id: `${s.id}-live`, kind: "live", time: null, sessionIdx: i });
    }
  });

  return (
    <div className="relative pl-8" data-testid="visit-timeline">
      {/* vertical rail */}
      <div className="absolute left-3 top-2 bottom-2 w-px bg-gradient-to-b from-blue-500/50 via-blue-500/25 to-transparent" />
      <ol className="space-y-4">
        {points.map((p, i) => {
          const isEntry = p.kind === "entry";
          const isLive = p.kind === "live";
          const color = isLive ? "bg-amber-500 border-amber-400" : isEntry ? "bg-blue-600 border-blue-400" : "bg-sky-500 border-sky-400";
          const Icon = isEntry ? LogInIcon : isLive ? Clock : LogOut;
          return (
            <motion.li
              key={p.id}
              initial={{ opacity: 0, x: -12 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.35, delay: i * 0.03 }}
              className="relative flex items-center gap-4"
              data-testid={`timeline-point-${p.id}`}
            >
              <div className={`absolute -left-8 h-6 w-6 rounded-full border-2 ${color} shadow-lg shadow-blue-900/50 flex items-center justify-center ${isLive ? "animate-pulse" : ""}`}>
                <Icon className="h-3 w-3 text-white" />
              </div>

              <div className="glass rounded-xl px-4 py-3 flex-1 flex items-center justify-between hover:-translate-y-0.5 transition-all duration-300">
                <div className="flex items-center gap-3">
                  <div className={`text-[10px] uppercase tracking-widest ${isEntry ? "text-blue-300" : isLive ? "text-amber-300" : "text-sky-300"}`}>
                    Visit {p.sessionIdx + 1} · {isEntry ? "Entry" : isLive ? "In progress" : "Exit"}
                  </div>
                  <div className="font-mono text-lg text-white">{isLive ? "—" : formatTime(p.time)}</div>
                </div>
                <div className="hidden md:flex items-center gap-3 text-[11px] text-slate-400">
                  {p.camera && <span className="font-mono">{p.camera}</span>}
                  {p.kind === "exit" && p.duration > 0 && (
                    <span className="rounded-full px-2 py-0.5 bg-blue-500/15 text-blue-300 border border-blue-500/25 font-medium">
                      {formatDurationShort(p.duration)}
                    </span>
                  )}
                </div>
              </div>
            </motion.li>
          );
        })}
      </ol>
    </div>
  );
}
