import React, { useCallback, useEffect, useState } from "react";
import api from "@/lib/api";
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid,
  BarChart, Bar, Legend, PieChart, Pie, Cell,
} from "recharts";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import PageHeader from "@/components/common/PageHeader";
import SectionCard from "@/components/common/SectionCard";
import VisitStatCard from "@/components/VisitStatCard";
import { ErrorState, EmptyState } from "@/components/common/EmptyState";
import { CardSkeleton } from "@/components/common/Loaders";
import { BarChart3, Clock, TrendingUp, TrendingDown, Activity, PieChart as PieIcon, LineChart as LineIcon } from "lucide-react";
import { formatDurationShort } from "@/lib/time";
import { chartTheme, chartPalette, colors } from "@/lib/theme";

const isEmpty = (arr) => !arr || arr.length === 0 || arr.every((d) => !d.entries && !d.exits && !d.value);

function TrendChart({ data }) {
  if (isEmpty(data)) return <EmptyState compact icon={LineIcon} title="No traffic in this period" />;
  return (
    <ResponsiveContainer width="100%" height={280}>
      <AreaChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
        <defs>
          <linearGradient id="an-entries" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={colors.primary} stopOpacity={0.18} />
            <stop offset="100%" stopColor={colors.primary} stopOpacity={0} />
          </linearGradient>
          <linearGradient id="an-exits" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={colors.info} stopOpacity={0.15} />
            <stop offset="100%" stopColor={colors.info} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid {...chartTheme.grid} />
        <XAxis dataKey="label" {...chartTheme.axis} />
        <YAxis allowDecimals={false} {...chartTheme.axis} />
        <Tooltip {...chartTheme.tooltip} />
        <Legend {...chartTheme.legend} iconType="circle" iconSize={8} />
        <Area type="monotone" name="Entries" dataKey="entries" stroke={colors.primary} strokeWidth={2} fill="url(#an-entries)" />
        <Area type="monotone" name="Exits" dataKey="exits" stroke={colors.info} strokeWidth={2} fill="url(#an-exits)" />
      </AreaChart>
    </ResponsiveContainer>
  );
}

function CompareBars({ data, height = 280 }) {
  if (isEmpty(data)) return <EmptyState compact icon={BarChart3} title="No traffic in this period" />;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }} barGap={4}>
        <CartesianGrid {...chartTheme.grid} />
        <XAxis dataKey="label" {...chartTheme.axis} />
        <YAxis allowDecimals={false} {...chartTheme.axis} />
        <Tooltip {...chartTheme.tooltip} />
        <Legend {...chartTheme.legend} iconType="circle" iconSize={8} />
        <Bar name="Entries" dataKey="entries" fill={colors.primary} radius={[4, 4, 0, 0]} maxBarSize={28} />
        <Bar name="Exits" dataKey="exits" fill={colors.info} radius={[4, 4, 0, 0]} maxBarSize={28} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export default function Analytics() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    setError(false);
    try {
      const { data } = await api.get("/analytics/overview");
      setData(data);
    } catch {
      setError(true);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const header = <PageHeader icon={BarChart3} title="Analytics" description="Showroom traffic patterns across days, weeks and months." />;

  if (error) return <div className="space-y-6">{header}<div className="surface"><ErrorState onRetry={load} /></div></div>;

  if (!data) {
    return (
      <div className="space-y-6">
        {header}
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">{Array.from({ length: 4 }).map((_, i) => <CardSkeleton key={i} />)}</div>
        <div className="grid gap-6 lg:grid-cols-2">{[0, 1].map((i) => <Skeleton key={i} className="h-80 rounded-xl" />)}</div>
      </div>
    );
  }

  const pieTotal = (data.pie || []).reduce((a, b) => a + (b.value || 0), 0);

  return (
    <div className="space-y-6" data-testid="analytics-page">
      {header}

      {data.stay_time ? (
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <VisitStatCard label="Average stay" value={formatDurationShort(data.stay_time.avg_seconds)} tone="blue" icon={Clock} testId="analytics-avg-stay" />
          <VisitStatCard label="Longest stay" value={formatDurationShort(data.stay_time.longest_seconds)} tone="emerald" icon={TrendingUp} testId="analytics-longest-stay" />
          <VisitStatCard label="Shortest stay" value={formatDurationShort(data.stay_time.shortest_seconds)} tone="amber" icon={TrendingDown} testId="analytics-shortest-stay" />
          <VisitStatCard label="Completed visits" value={(data.stay_time.completed_sessions ?? 0).toLocaleString()} tone="violet" icon={Activity} testId="analytics-completed" />
        </div>
      ) : null}

      <Tabs defaultValue="daily" className="space-y-4">
        <TabsList>
          <TabsTrigger value="daily" data-testid="tab-daily">Last 7 days</TabsTrigger>
          <TabsTrigger value="weekly" data-testid="tab-weekly">Last 4 weeks</TabsTrigger>
          <TabsTrigger value="monthly" data-testid="tab-monthly">Last 12 months</TabsTrigger>
        </TabsList>
        {["daily", "weekly", "monthly"].map((key) => (
          <TabsContent key={key} value={key} className="mt-0">
            <div className="grid gap-6 lg:grid-cols-2">
              <SectionCard icon={LineIcon} title="Traffic trend" description="Entries and exits over time"><TrendChart data={data[key]} /></SectionCard>
              <SectionCard icon={BarChart3} title="Entries vs exits" description="Side-by-side comparison"><CompareBars data={data[key]} /></SectionCard>
            </div>
          </TabsContent>
        ))}
      </Tabs>

      <div className="grid gap-6 lg:grid-cols-3">
        <SectionCard icon={PieIcon} title="Entry / exit split">
          {pieTotal === 0 ? (
            <EmptyState compact icon={PieIcon} title="No data yet" />
          ) : (
            <div className="relative">
              <ResponsiveContainer width="100%" height={260}>
                <PieChart>
                  <Pie data={data.pie} cx="50%" cy="45%" innerRadius={62} outerRadius={90} paddingAngle={3} dataKey="value" stroke="none">
                    {data.pie.map((entry, i) => <Cell key={entry.name} fill={chartPalette[i % chartPalette.length]} />)}
                  </Pie>
                  <Tooltip {...chartTheme.tooltip} />
                  <Legend {...chartTheme.legend} iconType="circle" iconSize={8} />
                </PieChart>
              </ResponsiveContainer>
              <div className="pointer-events-none absolute inset-x-0 top-[45%] -translate-y-1/2 text-center">
                <div className="text-2xl font-semibold tabular-nums">{pieTotal.toLocaleString()}</div>
                <div className="text-xs text-muted-foreground">total</div>
              </div>
            </div>
          )}
        </SectionCard>
        <SectionCard className="lg:col-span-2" icon={BarChart3} title="Monthly overview" description="Last 12 months">
          <CompareBars data={data.monthly} height={260} />
        </SectionCard>
      </div>
    </div>
  );
}
