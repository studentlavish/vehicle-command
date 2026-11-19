import React, { useEffect, useMemo, useState, useCallback } from "react";
import { useNavigate, useParams, Link } from "react-router-dom";
import api, { assetUrl, downloadBlob, errorMessage } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import VisitStatCard from "@/components/VisitStatCard";
import RangeFilters from "@/components/RangeFilters";
import VisitTimeline from "@/components/VisitTimeline";
import SectionCard from "@/components/common/SectionCard";
import StatusBadge from "@/components/common/StatusBadge";
import { EmptyState } from "@/components/common/EmptyState";
import { CardSkeleton, TableSkeletonRows } from "@/components/common/Loaders";
import { FORMATS } from "@/lib/exports";
import {
  ArrowLeft, Car, Phone, User, Fingerprint, Camera as CameraIcon, CalendarCheck, Clock, Activity,
  TrendingUp, LogIn as LogInIcon, LogOut, Loader2, ArrowRight, Download, History, ListTree,
  ChevronRight, Timer, TrendingDown, Hash,
} from "lucide-react";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { toast } from "sonner";
import { formatDate, formatTime, formatDuration, formatDurationShort, toISODate } from "@/lib/time";

function groupByDate(sessions) {
  const map = new Map();
  sessions.forEach((s) => {
    const key = s.visit_date || (s.entry_time || "").slice(0, 10);
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(s);
  });
  return Array.from(map.entries())
    .sort((a, b) => (a[0] < b[0] ? 1 : -1))
    .map(([d, list]) => [d, [...list].sort((x, y) => (x.entry_time < y.entry_time ? -1 : 1))]);
}

function InfoRow({ icon: Icon, label, value, mono }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2.5">
      <span className="flex items-center gap-2 text-sm text-muted-foreground"><Icon className="h-4 w-4" />{label}</span>
      <span className={`truncate text-right text-sm font-medium ${mono ? "font-mono" : ""}`}>{value || "—"}</span>
    </div>
  );
}

export default function VehicleDetail() {
  const { vehicleNumber } = useParams();
  const navigate = useNavigate();
  const [detail, setDetail] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [range, setRange] = useState("30d");
  const [customFrom, setCustomFrom] = useState(null);
  const [customTo, setCustomTo] = useState(null);
  const [exportBusy, setExportBusy] = useState(null);

  const rangeParams = useCallback(() => {
    const p = { range };
    if (range === "custom") { p.from_ = toISODate(customFrom); p.to = toISODate(customTo); }
    return p;
  }, [range, customFrom, customTo]);

  const loadDetail = useCallback(async () => {
    try {
      const { data } = await api.get(`/vehicles/master/${encodeURIComponent(vehicleNumber)}`);
      setDetail(data);
    } catch (e) {
      toast.error("Vehicle not found", { description: errorMessage(e) });
      navigate("/vehicles", { replace: true });
    }
  }, [vehicleNumber, navigate]);

  const loadSessions = useCallback(async () => {
    if (range === "custom" && (!customFrom || !customTo)) return;
    setLoading(true);
    try {
      const { data } = await api.get(`/vehicles/master/${encodeURIComponent(vehicleNumber)}/sessions`, { params: rangeParams() });
      setSessions(Array.isArray(data) ? data : []);
    } catch {
      setSessions([]);
    } finally {
      setLoading(false);
    }
  }, [vehicleNumber, range, customFrom, customTo, rangeParams]);

  useEffect(() => { setDetail(null); loadDetail(); }, [loadDetail]);
  useEffect(() => { loadSessions(); }, [loadSessions]);

  const grouped = useMemo(() => groupByDate(sessions), [sessions]);

  const doExport = async (fmt) => {
    setExportBusy(fmt);
    try {
      const res = await api.get("/visits/export", { params: { format: fmt, vehicle_number: vehicleNumber, ...rangeParams() }, responseType: "blob" });
      downloadBlob(res, `visits_${vehicleNumber}.${fmt}`);
      toast.success("Export downloaded", { description: fmt.toUpperCase() });
    } catch (e) {
      toast.error("Export failed", { description: errorMessage(e) });
    } finally {
      setExportBusy(null);
    }
  };

  if (!detail) {
    return (
      <div className="space-y-6" data-testid="vehicle-detail-loading">
        <Skeleton className="h-5 w-48" />
        <Skeleton className="h-10 w-64" />
        <div className="grid gap-6 lg:grid-cols-3">
          <Skeleton className="h-96 rounded-xl" />
          <div className="grid grid-cols-2 gap-4 lg:col-span-2">{Array.from({ length: 6 }).map((_, i) => <CardSkeleton key={i} />)}</div>
        </div>
      </div>
    );
  }

  const m = detail.master;
  const s = detail.summary;
  const a = detail.analytics;
  const insideNow = sessions.some((x) => x.status === "active");

  return (
    <div className="space-y-6" data-testid="vehicle-detail-page">
      {/* Breadcrumb + header */}
      <div className="space-y-3">
        <nav className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <Link to="/vehicles" className="hover:text-foreground">Vehicle records</Link>
          <ChevronRight className="h-3.5 w-3.5" />
          <span className="font-medium text-foreground">{m.vehicle_number}</span>
        </nav>
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div className="flex items-center gap-3">
            <Button variant="outline" size="icon" onClick={() => navigate(-1)} aria-label="Back" data-testid="detail-back">
              <ArrowLeft className="h-4 w-4" />
            </Button>
            <span className="plate px-3 py-1 text-xl">{m.vehicle_number}</span>
            {insideNow && <StatusBadge status="inside">Inside now</StatusBadge>}
          </div>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" disabled={!!exportBusy}>
                {exportBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />} Export visits
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {FORMATS.map((f) => (
                <DropdownMenuItem key={f.key} onSelect={() => doExport(f.key)} data-testid={`detail-export-${f.key}`}>
                  <f.icon className={`mr-2 h-4 w-4 ${f.className}`} /> {f.label}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Owner card */}
        <div className="surface overflow-hidden">
          <div className="relative flex aspect-video items-center justify-center bg-muted">
            {m.vehicle_image ? (
              <img src={assetUrl(m.vehicle_image)} alt={m.vehicle_number} className="h-full w-full object-cover" />
            ) : (
              <Car className="h-12 w-12 text-muted-foreground/50" />
            )}
          </div>
          <div className="divide-y px-5 py-2">
            <InfoRow icon={User} label="Owner" value={m.owner_name} />
            <InfoRow icon={Car} label="Model" value={m.vehicle_model} />
            <InfoRow icon={Phone} label="Phone" value={m.phone_number} />
            <InfoRow icon={Fingerprint} label="Customer ID" value={m.customer_id} mono />
            <InfoRow icon={CalendarCheck} label="First seen" value={formatDate(m.created_at)} />
          </div>
        </div>

        {/* Summary */}
        <div className="grid grid-cols-2 content-start gap-4 lg:col-span-2">
          <VisitStatCard testId="detail-stat-today-visits" label="Visits today" value={s.today_visits} sub={`${formatDurationShort(s.today_stay_seconds)} inside`} icon={LogInIcon} tone="blue" />
          <VisitStatCard testId="detail-stat-today-stay" label="Time inside today" value={formatDurationShort(s.today_stay_seconds)} sub={`${s.today_visits} visit${s.today_visits === 1 ? "" : "s"}`} icon={Clock} tone="sky" />
          <VisitStatCard testId="detail-stat-month-visits" label="Visits this month" value={s.month_visits} sub={`${formatDurationShort(s.month_stay_seconds)} inside`} icon={CalendarCheck} tone="violet" />
          <VisitStatCard testId="detail-stat-month-stay" label="Time inside this month" value={formatDurationShort(s.month_stay_seconds)} sub={`${s.month_visits} visit${s.month_visits === 1 ? "" : "s"}`} icon={Activity} tone="emerald" />
          <VisitStatCard testId="detail-stat-overall-visits" label="Total visits" value={s.overall_visits} sub="Last 180 days" icon={TrendingUp} tone="amber" />
          <VisitStatCard testId="detail-stat-overall-stay" label="Total time inside" value={formatDurationShort(s.overall_stay_seconds)} sub={`Average ${formatDurationShort(a.avg_seconds)}`} icon={Timer} tone="blue" />
        </div>
      </div>

      {/* Stay metrics */}
      <div className="surface grid grid-cols-2 divide-y md:grid-cols-4 md:divide-x md:divide-y-0">
        {[
          { label: "Average stay", value: formatDurationShort(a.avg_seconds), icon: Clock, id: "metric-avg" },
          { label: "Longest stay", value: formatDurationShort(a.longest_seconds), icon: TrendingUp, id: "metric-longest" },
          { label: "Shortest stay", value: formatDurationShort(a.shortest_seconds), icon: TrendingDown, id: "metric-shortest" },
          { label: "Total sessions", value: s.overall_visits, icon: Hash, id: "metric-total" },
        ].map((x) => (
          <div key={x.id} className="flex items-center gap-3 p-4" data-testid={x.id}>
            <x.icon className="h-4 w-4 text-muted-foreground" />
            <div>
              <div className="text-xs text-muted-foreground">{x.label}</div>
              <div className="text-lg font-semibold tabular-nums">{x.value}</div>
            </div>
          </div>
        ))}
      </div>

      <SnapshotStrip sessions={sessions} vehicleNumber={m.vehicle_number} />

      {/* Visits */}
      <SectionCard
        icon={History}
        title="Visits"
        description={loading ? "Loading…" : `${sessions.length} visit${sessions.length === 1 ? "" : "s"} in the selected range`}
        noPadding
      >
        <Tabs defaultValue="table">
          <div className="flex flex-col gap-3 border-b px-5 py-3 lg:flex-row lg:items-center lg:justify-between">
            <RangeFilters value={range} onChange={setRange} customFrom={customFrom} customTo={customTo} onCustom={(f, t) => { setCustomFrom(f); setCustomTo(t); setRange("custom"); }} />
            <TabsList>
              <TabsTrigger value="table" data-testid="tab-table"><History className="mr-1.5 h-3.5 w-3.5" />Table</TabsTrigger>
              <TabsTrigger value="timeline" data-testid="tab-timeline"><ListTree className="mr-1.5 h-3.5 w-3.5" />Timeline</TabsTrigger>
            </TabsList>
          </div>

          <TabsContent value="table" className="mt-0">
            <SessionsTable loading={loading} sessions={sessions} />
          </TabsContent>

          <TabsContent value="timeline" className="mt-0 p-5">
            {loading ? (
              <div className="space-y-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-11 w-full" />)}</div>
            ) : grouped.length === 0 ? (
              <EmptyState icon={Clock} title="No visits in this range" description="Pick a wider date range above." />
            ) : (
              <div className="space-y-8">
                {grouped.map(([day, group]) => (
                  <div key={day}>
                    <div className="mb-3 flex items-center justify-between">
                      <div className="flex items-center gap-2 text-sm font-semibold"><CalendarCheck className="h-4 w-4 text-primary" />{formatDate(day)}</div>
                      <span className="text-xs text-muted-foreground">{group.length} visit{group.length === 1 ? "" : "s"}</span>
                    </div>
                    <VisitTimeline sessions={group} />
                  </div>
                ))}
              </div>
            )}
          </TabsContent>
        </Tabs>
      </SectionCard>
    </div>
  );
}

function SnapshotStrip({ sessions, vehicleNumber }) {
  const [preview, setPreview] = useState(null);
  const shots = (sessions || []).filter((s) => s.entry_image && String(s.entry_image).startsWith("/api/snapshots/")).slice(0, 10);
  if (shots.length === 0) return null;
  return (
    <SectionCard icon={CameraIcon} title="Entry snapshots" description={`Latest ${shots.length} camera captures`} data-testid="snapshot-strip">
      <div className="flex gap-3 overflow-x-auto pb-1">
        {shots.map((s) => (
          <button key={s.id} onClick={() => setPreview(s)} className="group w-44 shrink-0 text-left" data-testid={`snap-${s.id}`}>
            <div className="aspect-video overflow-hidden rounded-lg border bg-muted transition-all group-hover:border-primary group-hover:shadow-card-hover">
              <img src={assetUrl(s.entry_image)} alt={`${vehicleNumber} entry`} className="h-full w-full object-cover" loading="lazy" />
            </div>
            <div className="mt-1.5 text-xs text-muted-foreground">{formatDate(s.entry_time)} · {formatTime(s.entry_time)}</div>
          </button>
        ))}
      </div>
      <Dialog open={!!preview} onOpenChange={(v) => !v && setPreview(null)}>
        <DialogContent className="max-w-4xl p-3" data-testid="snap-preview-overlay">
          <DialogTitle className="px-1 pt-1 text-sm">
            {vehicleNumber} · {preview && `${formatDate(preview.entry_time)} ${formatTime(preview.entry_time)}`}
            {preview?.entry_camera && <span className="ml-2 font-mono text-xs text-muted-foreground">{preview.entry_camera}</span>}
          </DialogTitle>
          {preview && <img src={assetUrl(preview.entry_image)} alt="Snapshot" className="max-h-[75vh] w-full rounded-lg object-contain bg-slate-900" />}
        </DialogContent>
      </Dialog>
    </SectionCard>
  );
}

function SessionsTable({ loading, sessions }) {
  return (
    <div className="overflow-x-auto">
      <table className="data-table">
        <thead>
          <tr>
            <th>Visit</th>
            <th>Date</th>
            <th>Entry</th>
            <th>Exit</th>
            <th>Duration</th>
            <th>Cameras</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {loading ? (
            <TableSkeletonRows rows={5} cols={7} />
          ) : sessions.length === 0 ? (
            <tr className="hover:bg-transparent"><td colSpan={7}><EmptyState icon={History} title="No visits in this range" description="Pick a wider date range above." /></td></tr>
          ) : (
            sessions.map((s, i) => (
              <tr key={s.id} data-testid={`session-row-${s.id}`}>
                <td className="font-medium tabular-nums">#{sessions.length - i}</td>
                <td className="whitespace-nowrap text-muted-foreground">{formatDate(s.entry_time)}</td>
                <td className="whitespace-nowrap tabular-nums">
                  <span className="inline-flex items-center gap-1.5"><LogInIcon className="h-3.5 w-3.5 text-primary" />{formatTime(s.entry_time)}</span>
                </td>
                <td className="whitespace-nowrap tabular-nums">
                  <span className="inline-flex items-center gap-1.5"><LogOut className="h-3.5 w-3.5 text-info" />{s.exit_time ? formatTime(s.exit_time) : "—"}</span>
                </td>
                <td className="whitespace-nowrap font-medium">{formatDuration(s.duration_seconds)}</td>
                <td className="font-mono text-xs text-muted-foreground">
                  <span className="inline-flex items-center gap-1">
                    {s.entry_camera || "—"}
                    {s.exit_camera ? <><ArrowRight className="h-3 w-3" />{s.exit_camera}</> : null}
                  </span>
                </td>
                <td><StatusBadge status={s.status} /></td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
