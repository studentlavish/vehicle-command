import React, { useEffect, useState } from "react";
import api from "@/lib/api";
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid,
  BarChart, Bar, Legend, PieChart, Pie, Cell,
} from "recharts";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { BarChart3 } from "lucide-react";

const COLORS = ["#3B82F6", "#0EA5E9", "#22C55E", "#EAB308"];

const tooltipStyle = {
  contentStyle: { background: "rgba(15,23,42,0.95)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 12, color: "#fff" },
  labelStyle: { color: "#94A3B8" },
};

export default function Analytics() {
  const [data, setData] = useState(null);
  useEffect(() => {
    (async () => {
      const { data } = await api.get("/analytics/overview");
      setData(data);
    })();
  }, []);

  if (!data) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-16 w-64 bg-white/5" />
        <div className="grid gap-4 md:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={`an-skel-${i}`} className="h-72 bg-white/5" />)}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6" data-testid="analytics-page">
      <div>
        <div className="inline-flex items-center gap-2 text-xs uppercase tracking-[0.25em] text-blue-400/80">
          <BarChart3 className="h-3.5 w-3.5" /> Insights
        </div>
        <h1 className="mt-2 text-4xl md:text-5xl font-bold tracking-tight">Analytics</h1>
        <p className="text-slate-400 mt-2 text-sm">Track showroom traffic patterns across days, weeks and months.</p>
      </div>

      <Tabs defaultValue="daily" className="w-full">
        <TabsList className="bg-white/5 border border-white/10 p-1">
          <TabsTrigger value="daily" data-testid="tab-daily" className="data-[state=active]:bg-blue-600 data-[state=active]:text-white">Daily (7d)</TabsTrigger>
          <TabsTrigger value="weekly" data-testid="tab-weekly" className="data-[state=active]:bg-blue-600 data-[state=active]:text-white">Weekly (28d)</TabsTrigger>
          <TabsTrigger value="monthly" data-testid="tab-monthly" className="data-[state=active]:bg-blue-600 data-[state=active]:text-white">Monthly (12m)</TabsTrigger>
        </TabsList>

        {["daily", "weekly", "monthly"].map((key) => (
          <TabsContent key={key} value={key}>
            <div className="grid gap-6 md:grid-cols-2">
              <div className="glass rounded-2xl p-5">
                <h3 className="font-semibold mb-4">Traffic Trend</h3>
                <ResponsiveContainer width="100%" height={280}>
                  <LineChart data={data[key]}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                    <XAxis dataKey="label" stroke="#94A3B8" fontSize={12} />
                    <YAxis stroke="#94A3B8" fontSize={12} />
                    <Tooltip {...tooltipStyle} />
                    <Legend wrapperStyle={{ color: "#94A3B8" }} />
                    <Line type="monotone" dataKey="entries" stroke="#3B82F6" strokeWidth={2.5} dot={false} />
                    <Line type="monotone" dataKey="exits" stroke="#0EA5E9" strokeWidth={2.5} dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
              <div className="glass rounded-2xl p-5">
                <h3 className="font-semibold mb-4">Entry vs Exit</h3>
                <ResponsiveContainer width="100%" height={280}>
                  <BarChart data={data[key]}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                    <XAxis dataKey="label" stroke="#94A3B8" fontSize={12} />
                    <YAxis stroke="#94A3B8" fontSize={12} />
                    <Tooltip {...tooltipStyle} />
                    <Legend wrapperStyle={{ color: "#94A3B8" }} />
                    <Bar dataKey="entries" fill="#3B82F6" radius={[6, 6, 0, 0]} />
                    <Bar dataKey="exits" fill="#0EA5E9" radius={[6, 6, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </TabsContent>
        ))}
      </Tabs>

      <div className="grid gap-6 md:grid-cols-3">
        <div className="glass rounded-2xl p-5 md:col-span-1">
          <h3 className="font-semibold mb-4">Entry vs Exit Split</h3>
          <ResponsiveContainer width="100%" height={260}>
            <PieChart>
              <Pie data={data.pie} cx="50%" cy="50%" innerRadius={60} outerRadius={90} paddingAngle={4} dataKey="value">
                {data.pie.map((entry, i) => <Cell key={`cell-${entry.name}`} fill={COLORS[i % COLORS.length]} />)}
              </Pie>
              <Tooltip {...tooltipStyle} />
              <Legend wrapperStyle={{ color: "#94A3B8" }} />
            </PieChart>
          </ResponsiveContainer>
        </div>
        <div className="glass rounded-2xl p-5 md:col-span-2">
          <h3 className="font-semibold mb-4">Monthly Overview</h3>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={data.monthly}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis dataKey="label" stroke="#94A3B8" fontSize={12} />
              <YAxis stroke="#94A3B8" fontSize={12} />
              <Tooltip {...tooltipStyle} />
              <Bar dataKey="entries" fill="#3B82F6" radius={[6, 6, 0, 0]} />
              <Bar dataKey="exits" fill="#0EA5E9" radius={[6, 6, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
