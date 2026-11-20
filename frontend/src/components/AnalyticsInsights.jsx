import React, { useCallback, useEffect, useState } from "react";
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, Legend,
} from "recharts";
import api from "@/lib/api";
import SectionCard from "@/components/common/SectionCard";
import VisitStatCard from "@/components/VisitStatCard";
import { EmptyState, ErrorState } from "@/components/common/EmptyState";
import { Skeleton } from "@/components/ui/skeleton";
import { Grid3x3, Repeat, UserPlus, UserCheck, Crosshair, Camera } from "lucide-react";
import { formatDurationShort } from "@/lib/time";
import { chartTheme } from "@/lib/theme";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)"];
const pct = (x) => (x == null ? "—" : `${Math.round(x * 100)}%`);
const hourLabel = (h) => `${String(h).padStart(2, "0")}:00`;

function useData(url, params) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(false);
  const key = JSON.stringify(params);
  const load = useCallback(async () => {
    setError(false);
    try {
      const { data } = await api.get(url, { params: JSON.parse(key) });
      setData(data);
    } catch {
      setError(true);
    }
  }, [url, key]);
  useEffect(() => { load(); }, [load]);
  return { data, error, load };
}

/** Weekday × hour grid of entries. One hue, light → dark = fewer → more cars. */
function Heatmap({ grid, busiest }) {
  const [hover, setHover] = useState(null);
  const max = Math.max(1, ...grid.flat());
  const hours = Array.from({ length: 24 }, (_, h) => h);
  const shown = hover || (busiest ? { d: busiest.weekday, h: busiest.hour, v: busiest.entries, peak: true } : null);
  return (
    <div>
      <div className="mb-3 h-5 text-xs text-muted-foreground" aria-live="polite" data-testid="heatmap-readout">
        {shown ? (
          <>
            {shown.peak ? "Busiest: " : ""}
            <span className="font-medium text-foreground">{DAYS[shown.d]} {hourLabel(shown.h)}–{hourLabel((shown.h + 1) % 24)}</span>
            {" · "}{shown.v} entr{shown.v === 1 ? "y" : "ies"}
          </>
        ) : "Hover a cell to see the count"}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] table-fixed border-separate" style={{ borderSpacing: 2 }} role="grid" aria-label="Entries by weekday and hour">
          <thead>
            <tr>
              <th className="w-10" style={{ width: 40 }} />
              {hours.map((h) => (
                <th key={h} className="text-[10px] font-normal text-muted-foreground" scope="col">
                  {h % 3 === 0 ? String(h).padStart(2, "0") : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody onMouseLeave={() => setHover(null)}>
            {grid.map((row, d) => (
              <tr key={d}>
                <th scope="row" className="pr-2 text-left text-[11px] font-medium text-muted-foreground">{DAYS[d]}</th>
                {row.map((v, h) => {
                  const a = v ? 0.12 + 0.88 * (v / max) : 0;
                  return (
                    <td
                      key={h}
                      onMouseEnter={() => setHover({ d, h, v })}
                      title={`${DAYS[d]} ${hourLabel(h)} — ${v}`}
                      aria-label={`${DAYS[d]} ${hourLabel(h)}: ${v} entries`}
                      className="h-6 rounded-[4px] p-0 transition-shadow hover:ring-2 hover:ring-foreground/40"
                      style={{ background: v ? `hsl(var(--primary) / ${a.toFixed(3)})` : "hsl(var(--muted))" }}
                    />
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex items-center justify-end gap-2 text-[11px] text-muted-foreground">
        <span>Fewer</span>
        {[0.12, 0.34, 0.56, 0.78, 1].map((a) => (
          <span key={a} className="h-3 w-5 rounded-[3px]" style={{ background: `hsl(var(--primary) / ${a})` }} />
        ))}
        <span>More ({max} max)</span>
      </div>
    </div>
  );
}

function AccuracyChart({ data }) {
  // Chart the four busiest cameras (fixed colours per camera, by name order); the table lists all.
  const cams = [...data.totals].sort((a, b) => b.reads - a.reads).slice(0, 4).map((c) => c.camera).sort();
  const rows = data.series.map((r) => ({ ...r, label: r.date.slice(5) }));
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={rows} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
        <CartesianGrid {...chartTheme.grid} />
        <XAxis dataKey="label" {...chartTheme.axis} minTickGap={24} />
        <YAxis domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} unit="%" {...chartTheme.axis} />
        <Tooltip {...chartTheme.tooltip} cursor={{ stroke: "hsl(var(--muted-foreground))", strokeDasharray: "3 3" }}
                 formatter={(v, name) => [v == null ? "no reads" : `${v}%`, name]} />
        <Legend {...chartTheme.legend} iconType="plainline"
                formatter={(name) => <span className="font-mono text-xs text-muted-foreground">{name}</span>} />
        {cams.map((c, i) => (
          <Line key={c} type="monotone" dataKey={c} name={c} stroke={SERIES[i]} strokeWidth={2}
                dot={false} activeDot={{ r: 4, strokeWidth: 2, stroke: "hsl(var(--card))" }} connectNulls />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

/** Staffing (busy hours, repeat visitors, stay time) and per-camera reading accuracy. */
export default function AnalyticsInsights() {
  const insights = useData("/analytics/insights", { days: 28 });
  const accuracy = useData("/analytics/accuracy", { days: 30 });
  const s = insights.data;
  const a = accuracy.data;

  return (
    <div className="space-y-6" data-testid="analytics-insights">
      {insights.error ? (
        <div className="surface"><ErrorState onRetry={insights.load} /></div>
      ) : !s ? (
        <Skeleton className="h-72 rounded-xl" />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <VisitStatCard label="Returning vehicles" value={pct(s.repeat.rate)} sub={`${s.repeat.returning_vehicles} of ${s.repeat.unique_vehicles} · last ${s.days} days`} tone="blue" icon={Repeat} testId="insight-repeat" />
            <VisitStatCard label="Unique vehicles" value={s.repeat.unique_vehicles.toLocaleString()} sub={`last ${s.days} days`} tone="violet" icon={Grid3x3} testId="insight-unique" />
            <VisitStatCard label="Avg stay · first visit" value={formatDurationShort(s.dwell.new_avg_seconds)} sub={`${s.dwell.new_visits} visits`} tone="amber" icon={UserPlus} testId="insight-dwell-new" />
            <VisitStatCard label="Avg stay · returning" value={formatDurationShort(s.dwell.returning_avg_seconds)} sub={`${s.dwell.returning_visits} visits`} tone="emerald" icon={UserCheck} testId="insight-dwell-returning" />
          </div>
          <SectionCard icon={Grid3x3} title="Busy hours" description={`Entries by weekday and hour, last ${s.days} days — plan staff around the dark cells`}>
            {s.heatmap.flat().every((v) => !v)
              ? <EmptyState compact icon={Grid3x3} title="No entries in this period" />
              : <Heatmap grid={s.heatmap} busiest={s.busiest} />}
          </SectionCard>
        </>
      )}

      <SectionCard icon={Crosshair} title="Reader accuracy by camera"
                   description="Share of reads saved without a person, per day. A falling line usually means a dirty lens or a changed angle.">
        {accuracy.error ? (
          <ErrorState onRetry={accuracy.load} className="py-6" />
        ) : !a ? (
          <Skeleton className="h-64 w-full" />
        ) : a.cameras.length === 0 ? (
          <EmptyState compact icon={Camera} title="No camera reads in the last 30 days" />
        ) : (
          <div className="space-y-5">
            <AccuracyChart data={a} />
            <div className="overflow-x-auto">
              <table className="data-table" data-testid="accuracy-table">
                <thead>
                  <tr><th>Camera</th><th className="!text-right">Reads</th><th className="!text-right">Saved automatically</th>
                    <th className="!text-right">Sent to review</th><th className="!text-right">Corrected by reviewer</th><th className="!text-right">Discarded</th></tr>
                </thead>
                <tbody>
                  {a.totals.map((c) => (
                    <tr key={c.camera}>
                      <td className="font-mono text-xs font-semibold">{c.camera}</td>
                      <td className="text-right tabular-nums">{c.reads}</td>
                      <td className="text-right tabular-nums">{pct(c.auto_rate)}</td>
                      <td className="text-right tabular-nums">{c.reviewed}</td>
                      <td className="text-right tabular-nums">{c.corrected}{c.correction_rate != null ? ` (${pct(c.correction_rate)})` : ""}</td>
                      <td className="text-right tabular-nums">{c.rejected}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </SectionCard>
    </div>
  );
}
