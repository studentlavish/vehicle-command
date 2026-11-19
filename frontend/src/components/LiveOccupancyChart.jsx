import React, { useEffect, useRef, useState } from "react";
import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid } from "recharts";
import { Car, TrendingUp } from "lucide-react";
import api from "@/lib/api";
import SectionCard from "@/components/common/SectionCard";
import { EmptyState } from "@/components/common/EmptyState";
import { chartTheme, colors } from "@/lib/theme";

/** Cars-inside over the last ~2 hours. Polls every 30s and reacts to live bumps. */
export default function LiveOccupancyChart({ liveValue }) {
  const [samples, setSamples] = useState([]);
  const lastLiveRef = useRef(null);

  const push = (v) =>
    setSamples((prev) => [...prev, { t: Date.now(), v: Number(v) || 0 }].slice(-240));

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const { data } = await api.get("/dashboard/stats");
        if (!cancelled) push(data?.cars_inside?.value ?? 0);
      } catch { /* ignore — card shows last known value */ }
    };
    tick();
    const i = setInterval(tick, 30000);
    return () => { cancelled = true; clearInterval(i); };
  }, []);

  useEffect(() => {
    if (liveValue == null || lastLiveRef.current === liveValue) return;
    lastLiveRef.current = liveValue;
    push(liveValue);
  }, [liveValue]);

  const values = samples.map((s) => s.v);
  const current = values.length ? values[values.length - 1] : 0;
  const peak = values.length ? Math.max(...values) : 0;
  const low = values.length ? Math.min(...values) : 0;
  const data = samples.map((s) => ({ ...s, label: new Date(s.t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) }));

  return (
    <SectionCard
      icon={TrendingUp}
      title="Live occupancy"
      description="Cars inside the showroom · rolling 2 hours"
      data-testid="live-occupancy-card"
    >
      <div className="mb-4 flex items-end gap-6">
        <div>
          <div className="flex items-center gap-2 text-3xl font-semibold tabular-nums" data-testid="occupancy-current">
            <Car className="h-6 w-6 text-success" /> {current}
          </div>
          <div className="text-xs text-muted-foreground">inside right now</div>
        </div>
        <div className="flex gap-4 text-sm">
          <div><div className="text-xs text-muted-foreground">Peak</div><div className="font-semibold tabular-nums">{peak}</div></div>
          <div><div className="text-xs text-muted-foreground">Low</div><div className="font-semibold tabular-nums">{low}</div></div>
        </div>
      </div>
      {data.length < 2 ? (
        <EmptyState compact icon={TrendingUp} title="Collecting data" description="The chart fills in as samples arrive every 30 seconds." />
      ) : (
        <div className="h-40" data-testid="occupancy-spark">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 4, right: 4, left: -24, bottom: 0 }}>
              <defs>
                <linearGradient id="occ-fill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={colors.success} stopOpacity={0.2} />
                  <stop offset="100%" stopColor={colors.success} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid {...chartTheme.grid} />
              <XAxis dataKey="label" {...chartTheme.axis} minTickGap={40} />
              <YAxis allowDecimals={false} {...chartTheme.axis} />
              <Tooltip {...chartTheme.tooltip} formatter={(v) => [v, "Inside"]} />
              <Area type="monotone" dataKey="v" stroke={colors.success} strokeWidth={2} fill="url(#occ-fill)" isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </SectionCard>
  );
}
