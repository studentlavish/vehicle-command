import React, { useEffect, useMemo, useState, useCallback } from "react";
import { useNavigate, useParams } from "react-router-dom";
import api from "@/lib/api";
import { motion } from "framer-motion";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import VisitStatCard from "@/components/VisitStatCard";
import RangeFilters from "@/components/RangeFilters";
import VisitTimeline from "@/components/VisitTimeline";
import {
  ArrowLeft, Car, Phone, User, Fingerprint, Camera as CameraIcon,
  CalendarCheck, Clock, Activity, TrendingUp, LogIn as LogInIcon, LogOut,
  FileSpreadsheet, FileText, FileDown, Loader2, Sparkles, ArrowRight,
} from "lucide-react";
import { toast } from "sonner";
import { formatDate, formatTime, formatDuration, formatDurationShort } from "@/lib/time";

const EXPORTS = [
  { key: "xlsx", label: "Excel", icon: FileSpreadsheet, tone: "text-emerald-300 border-emerald-500/30 hover:bg-emerald-500/10" },
  { key: "pdf", label: "PDF", icon: FileText, tone: "text-rose-300 border-rose-500/30 hover:bg-rose-500/10" },
  { key: "csv", label: "CSV", icon: FileDown, tone: "text-sky-300 border-sky-500/30 hover:bg-sky-500/10" },
];

const statusStyle = {
  active: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  completed: "bg-slate-500/15 text-slate-300 border-slate-500/30",
};

function groupByDate(sessions) {
  const map = new Map();
  sessions.forEach((s) => {
    const key = s.visit_date || (s.entry_time || "").slice(0, 10);
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(s);
  });
  return Array.from(map.entries()).sort((a, b) => (a[0] < b[0] ? 1 : -1));
}

export default function VehicleDetail() {
  const { vehicleNumber } = useParams();
  const navigate = useNavigate();
  const [detail, setDetail] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [range, setRange] = useState("today");
  const [customFrom, setCustomFrom] = useState(null);
  const [customTo, setCustomTo] = useState(null);
  const [exportBusy, setExportBusy] = useState(null);

  const loadDetail = useCallback(async () => {
    try {
      const { data } = await api.get(`/vehicles/master/${encodeURIComponent(vehicleNumber)}`);
      setDetail(data);
    } catch (e) {
      toast.error("Vehicle not found", { description: e.response?.data?.detail || e.message });
      navigate("/vehicles");
    }
  }, [vehicleNumber, navigate]);

  const loadSessions = useCallback(async () => {
    setLoading(true);
    try {
      const params = { range };
      if (range === "custom") {
        if (customFrom) params.from_ = new Date(customFrom).toISOString().slice(0, 10);
        if (customTo) params.to = new Date(customTo).toISOString().slice(0, 10);
      }
      const { data } = await api.get(`/vehicles/master/${encodeURIComponent(vehicleNumber)}/sessions`, { params });
      setSessions(data);
    } finally {
      setLoading(false);
    }
  }, [vehicleNumber, range, customFrom, customTo]);

  useEffect(() => { loadDetail(); }, [loadDetail]);
  useEffect(() => { loadSessions(); }, [loadSessions]);

  const grouped = useMemo(() => groupByDate(sessions), [sessions]);

  const doExport = async (fmt) => {
    setExportBusy(fmt);
    try {
      const params = { format: fmt, vehicle_number: vehicleNumber, range };
      if (range === "custom") {
        if (customFrom) params.from_ = new Date(customFrom).toISOString().slice(0, 10);
        if (customTo) params.to = new Date(customTo).toISOString().slice(0, 10);
      }
      const res = await api.get("/visits/export", { params, responseType: "blob" });
      const blob = new Blob([res.data]);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `visits_${vehicleNumber}.${fmt}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast.success(`${fmt.toUpperCase()} export ready`);
    } catch (e) {
      toast.error("Export failed", { description: e.message });
    } finally {
      setExportBusy(null);
    }
  };

  if (!detail) {
    return (
      <div className="space-y-4" data-testid="vehicle-detail-loading">
        <Skeleton className="h-14 w-72 bg-white/5" />
        <div className="grid gap-4 md:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={`vd-skel-${i}`} className="h-28 bg-white/5" />)}
        </div>
        <Skeleton className="h-64 bg-white/5" />
      </div>
    );
  }

  const m = detail.master;
  const s = detail.summary;
  const a = detail.analytics;

  return (
    <div className="space-y-6" data-testid="vehicle-detail-page">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3">
        <div className="flex items-center gap-3">
          <Button variant="ghost" onClick={() => navigate(-1)} className="h-9 text-slate-300 hover:text-white hover:bg-white/5" data-testid="detail-back">
            <ArrowLeft className="h-4 w-4 mr-2" /> Back
          </Button>
          <div>
            <div className="inline-flex items-center gap-2 text-xs uppercase tracking-[0.25em] text-blue-400/80">
              <Sparkles className="h-3.5 w-3.5" /> Vehicle Profile
            </div>
            <h1 className="mt-1 text-3xl md:text-4xl font-bold tracking-tight font-mono">{m.vehicle_number}</h1>
          </div>
        </div>
      </div>

      {/* Master info + image */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
        className="grid gap-6 lg:grid-cols-3"
      >
        <div className="glass rounded-2xl overflow-hidden lg:col-span-1">
          <div className="aspect-video relative bg-gradient-to-br from-slate-800 to-slate-900">
            {m.vehicle_image ? (
              <img src={m.vehicle_image} alt={m.vehicle_number} className="w-full h-full object-cover" />
            ) : (
              <div className="w-full h-full flex items-center justify-center"><Car className="h-14 w-14 text-slate-600" /></div>
            )}
            <div className="absolute inset-0 bg-gradient-to-t from-[#0F172A] via-transparent" />
            <Badge className="absolute top-3 right-3 bg-blue-500/20 text-blue-200 border border-blue-500/30 font-mono">{m.customer_id}</Badge>
          </div>
          <div className="p-5 space-y-3">
            <InfoRow icon={User} label="Owner Name" value={m.owner_name} />
            <InfoRow icon={Car} label="Model" value={m.vehicle_model || "—"} />
            <InfoRow icon={Phone} label="Phone" value={m.phone_number || "—"} />
            <InfoRow icon={Fingerprint} label="Customer ID" value={m.customer_id} />
            <InfoRow icon={CalendarCheck} label="First Seen" value={formatDate(m.created_at)} />
          </div>
        </div>

        {/* Summary cards */}
        <div className="lg:col-span-2 grid grid-cols-2 gap-4 content-start">
          <VisitStatCard testId="detail-stat-today-visits" label="Today's Visits" value={s.today_visits} sub={formatDurationShort(s.today_stay_seconds) + " total"} icon={LogInIcon} tone="blue" delay={0} />
          <VisitStatCard testId="detail-stat-today-stay" label="Today's Stay Time" value={formatDurationShort(s.today_stay_seconds)} sub={`${s.today_visits} visits`} icon={Clock} tone="sky" delay={0.05} />
          <VisitStatCard testId="detail-stat-month-visits" label="This Month" value={s.month_visits} sub={formatDurationShort(s.month_stay_seconds) + " total"} icon={CalendarCheck} tone="violet" delay={0.1} />
          <VisitStatCard testId="detail-stat-month-stay" label="Monthly Stay" value={formatDurationShort(s.month_stay_seconds)} sub={`${s.month_visits} visits`} icon={Activity} tone="emerald" delay={0.15} />
          <VisitStatCard testId="detail-stat-overall-visits" label="Overall Visits" value={s.overall_visits} sub={`Last ${180} days`} icon={TrendingUp} tone="amber" delay={0.2} />
          <VisitStatCard testId="detail-stat-overall-stay" label="Overall Stay Time" value={formatDurationShort(s.overall_stay_seconds)} sub={`Avg ${formatDurationShort(a.avg_seconds)}`} icon={Clock} tone="blue" delay={0.25} />
        </div>
      </motion.div>

      {/* Analytics micro-strip */}
      <div className="grid gap-4 grid-cols-2 md:grid-cols-4">
        <MetricPill label="Average Stay" value={formatDurationShort(a.avg_seconds)} testId="metric-avg" />
        <MetricPill label="Longest Stay" value={formatDurationShort(a.longest_seconds)} testId="metric-longest" />
        <MetricPill label="Shortest Stay" value={formatDurationShort(a.shortest_seconds)} testId="metric-shortest" />
        <MetricPill label="Total Sessions" value={s.overall_visits} testId="metric-total" />
      </div>

      {/* Latest entry snapshots gallery */}
      <SnapshotStrip sessions={sessions} vehicleNumber={m.vehicle_number} />

      {/* Range filters + Export */}
      <div className="flex flex-col md:flex-row gap-3 md:items-center md:justify-between">
        <RangeFilters value={range} onChange={setRange} customFrom={customFrom} customTo={customTo} onCustom={(f, t) => { setCustomFrom(f); setCustomTo(t); setRange("custom"); }} />
        <div className="glass rounded-2xl p-2 flex gap-2">
          {EXPORTS.map((f) => (
            <Button
              key={f.key}
              size="sm"
              variant="outline"
              onClick={() => doExport(f.key)}
              disabled={exportBusy === f.key}
              className={`bg-white/5 border ${f.tone}`}
              data-testid={`detail-export-${f.key}`}
            >
              {exportBusy === f.key ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <f.icon className="h-4 w-4 mr-2" />}
              {f.label}
            </Button>
          ))}
        </div>
      </div>

      {/* Timeline + Table tabs */}
      <Tabs defaultValue="table" className="w-full">
        <TabsList className="bg-white/5 border border-white/10 p-1">
          <TabsTrigger value="table" data-testid="tab-table" className="data-[state=active]:bg-blue-600 data-[state=active]:text-white">Visit History</TabsTrigger>
          <TabsTrigger value="timeline" data-testid="tab-timeline" className="data-[state=active]:bg-blue-600 data-[state=active]:text-white">Timeline</TabsTrigger>
        </TabsList>

        <TabsContent value="table">
          <SessionsTable loading={loading} sessions={sessions} />
        </TabsContent>

        <TabsContent value="timeline">
          <div className="glass rounded-2xl p-6">
            {loading ? <Skeleton className="h-64 bg-white/5" /> : (
              <div className="space-y-8">
                {grouped.length === 0 ? (
                  <div className="text-center text-slate-400 text-sm py-10">No sessions to visualise for this range.</div>
                ) : grouped.map(([day, group]) => (
                  <div key={day}>
                    <div className="mb-4 flex items-center gap-3">
                      <div className="h-8 w-8 rounded-lg bg-blue-500/15 border border-blue-500/30 flex items-center justify-center">
                        <CalendarCheck className="h-4 w-4 text-blue-400" />
                      </div>
                      <div>
                        <div className="text-[11px] uppercase tracking-widest text-slate-400">Day</div>
                        <div className="text-sm font-medium">{formatDate(day)}</div>
                      </div>
                      <Badge className="ml-auto bg-white/5 text-slate-200 border-white/10">{group.length} visits</Badge>
                    </div>
                    <VisitTimeline sessions={group} />
                  </div>
                ))}
              </div>
            )}
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}

function InfoRow({ icon: Icon, label, value }) {
  return (
    <div className="flex items-center gap-3">
      <div className="h-9 w-9 rounded-lg bg-white/5 border border-white/10 flex items-center justify-center">
        <Icon className="h-4 w-4 text-blue-400" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-[11px] uppercase tracking-widest text-slate-400">{label}</div>
        <div className="text-sm font-medium truncate">{value}</div>
      </div>
    </div>
  );
}

function MetricPill({ label, value, testId }) {
  return (
    <div className="glass rounded-xl p-4" data-testid={testId}>
      <div className="text-[11px] uppercase tracking-widest text-slate-400">{label}</div>
      <div className="text-xl font-bold mt-1">{value}</div>
    </div>
  );
}

function SnapshotStrip({ sessions, vehicleNumber }) {
  const base = process.env.REACT_APP_BACKEND_URL || "";
  const [preview, setPreview] = React.useState(null);
  const shots = (sessions || [])
    .filter((s) => s.entry_image && String(s.entry_image).startsWith("/api/snapshots/"))
    .slice(0, 8);
  if (shots.length === 0) return null;
  return (
    <div className="glass rounded-2xl p-5" data-testid="snapshot-strip">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <CameraIcon className="h-4 w-4 text-blue-400" />
          <h3 className="font-semibold">Latest Entry Snapshots</h3>
        </div>
        <div className="text-[11px] text-slate-400">{shots.length} of {sessions.length}</div>
      </div>
      <div className="flex gap-3 overflow-x-auto pb-1">
        {shots.map((s) => (
          <button
            key={s.id}
            onClick={() => setPreview(`${base}${s.entry_image}`)}
            className="shrink-0 w-40 group"
            data-testid={`snap-${s.id}`}
          >
            <div className="rounded-xl overflow-hidden border border-white/10 aspect-video bg-black/30 group-hover:border-blue-500/40 transition-colors">
              <img
                src={`${base}${s.entry_image}`}
                alt={`${vehicleNumber} entry`}
                className="w-full h-full object-cover"
                loading="lazy"
              />
            </div>
            <div className="text-[11px] text-slate-400 mt-1 font-mono truncate">{formatTime(s.entry_time)}</div>
          </button>
        ))}
      </div>
      {preview ? (
        <div
          className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-6"
          onClick={() => setPreview(null)}
          data-testid="snap-preview-overlay"
        >
          <img src={preview} alt="snapshot preview" className="max-w-[92vw] max-h-[88vh] rounded-xl border border-white/10" />
        </div>
      ) : null}
    </div>
  );
}

function SessionsTable({ loading, sessions }) {
  return (
    <div className="glass rounded-2xl overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-slate-400 text-[11px] uppercase tracking-widest border-b border-white/5">
              <th className="px-5 py-3 font-normal">Visit #</th>
              <th className="px-5 py-3 font-normal">Date</th>
              <th className="px-5 py-3 font-normal">Entry</th>
              <th className="px-5 py-3 font-normal">Exit</th>
              <th className="px-5 py-3 font-normal">Duration</th>
              <th className="px-5 py-3 font-normal">Cameras</th>
              <th className="px-5 py-3 font-normal">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {loading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <tr key={`s-skel-${i}`}><td colSpan={7} className="p-3"><Skeleton className="h-8 bg-white/5" /></td></tr>
              ))
            ) : sessions.length === 0 ? (
              <tr><td colSpan={7} className="text-center py-14 text-slate-400">No sessions in this range.</td></tr>
            ) : (
              sessions.map((s, i) => (
                <tr key={s.id} className="hover:bg-white/[0.03]" data-testid={`session-row-${s.id}`}>
                  <td className="px-5 py-3 font-mono text-white">Visit {i + 1}</td>
                  <td className="px-5 py-3 text-slate-300">{formatDate(s.entry_time)}</td>
                  <td className="px-5 py-3 font-mono text-blue-300 flex items-center gap-1"><LogInIcon className="h-3.5 w-3.5" />{formatTime(s.entry_time)}</td>
                  <td className="px-5 py-3 font-mono text-sky-300 flex items-center gap-1"><LogOut className="h-3.5 w-3.5" />{s.exit_time ? formatTime(s.exit_time) : "—"}</td>
                  <td className="px-5 py-3 text-slate-200 font-medium">{formatDuration(s.duration_seconds)}</td>
                  <td className="px-5 py-3 text-[11px] text-slate-400 font-mono">
                    <div className="flex items-center gap-1"><CameraIcon className="h-3 w-3" />{s.entry_camera || "—"}</div>
                    {s.exit_camera ? <div className="flex items-center gap-1 mt-0.5"><ArrowRight className="h-3 w-3" />{s.exit_camera}</div> : null}
                  </td>
                  <td className="px-5 py-3">
                    <Badge className={`${statusStyle[s.status] || statusStyle.completed} border capitalize`}>{s.status === "active" ? "In Progress" : "Completed"}</Badge>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
