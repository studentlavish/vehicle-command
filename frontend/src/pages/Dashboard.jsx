import React, { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import StatCard from "@/components/StatCard";
import VehicleDetailsDialog from "@/components/VehicleDetailsDialog";
import AgentHealthCard from "@/components/AgentHealthCard";
import LiveOccupancyChart from "@/components/LiveOccupancyChart";
import PageHeader from "@/components/common/PageHeader";
import SectionCard from "@/components/common/SectionCard";
import StatusBadge from "@/components/common/StatusBadge";
import VehiclePlate from "@/components/common/VehiclePlate";
import { EmptyState, ErrorState } from "@/components/common/EmptyState";
import { CardSkeleton, TableSkeletonRows } from "@/components/common/Loaders";
import useLiveEvents from "@/hooks/useLiveEvents";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  LogIn as LogInIcon, LogOut, Car, Database, Users as UsersIcon, CalendarRange,
  Video, VideoOff, Eye, ArrowRight, Clock, RefreshCw, LayoutDashboard,
} from "lucide-react";
import { formatDateTime, elapsed, greeting } from "@/lib/time";

function CamerasCard() {
  const [cams, setCams] = useState(null);
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const { data } = await api.get("/cameras");
        if (alive) setCams(Object.values(data || {}));
      } catch {
        if (alive) setCams((c) => c || []);
      }
    };
    load();
    const t = setInterval(load, 10000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  const online = (cams || []).filter((c) => c.status === "online").length;

  return (
    <SectionCard
      icon={Video}
      title="Cameras"
      description={cams ? `${online} of ${cams.length} online` : "Checking camera status…"}
      actions={
        <Button asChild variant="ghost" size="sm">
          <Link to="/live">Live view <ArrowRight className="h-3.5 w-3.5" /></Link>
        </Button>
      }
      data-testid="live-camera-card"
    >
      {cams === null ? (
        <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-12 w-full" />)}</div>
      ) : cams.length === 0 ? (
        <EmptyState
          compact
          icon={VideoOff}
          title="No cameras connected"
          description="Connect a camera from Live Monitoring or start the local camera agent."
          action={<Button asChild size="sm" variant="outline"><Link to="/live">Open Live Monitoring</Link></Button>}
        />
      ) : (
        <ul className="space-y-2">
          {cams.slice(0, 5).map((c) => (
            <li key={c.camera_id} className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2.5">
              <div className="flex min-w-0 items-center gap-3">
                <div className={c.status === "online" ? "icon-tile-sm" : "icon-tile-sm bg-muted text-muted-foreground"}>
                  {c.status === "online" ? <Video className="h-4 w-4" /> : <VideoOff className="h-4 w-4" />}
                </div>
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{c.label || c.camera_id}</div>
                  <div className="font-mono text-[11px] text-muted-foreground">{c.camera_id}</div>
                </div>
              </div>
              <StatusBadge status={c.status} />
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}

export default function Dashboard() {
  const { user } = useAuth();
  const [stats, setStats] = useState(null);
  const [recent, setRecent] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [selected, setSelected] = useState(null);
  const [open, setOpen] = useState(false);

  const load = useCallback(async (soft = false) => {
    soft ? setRefreshing(true) : setLoading(true);
    setError(false);
    try {
      const [s, v] = await Promise.all([
        api.get("/dashboard/stats"),
        api.get("/vehicles", { params: { limit: 8 } }),
      ]);
      setStats(s.data);
      setRecent(Array.isArray(v.data) ? v.data : v.data?.items || []);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Live updates from the event bus
  useLiveEvents((msg) => {
    if (msg.type !== "entry.recorded") return;
    const s = msg.session || {};
    const owner = msg.owner || {};
    setRecent((prev) => {
      const row = {
        id: s.id,
        vehicle_number: msg.vehicle_number,
        owner_name: owner.owner_name || "Unknown owner",
        contact_number: owner.phone_number || "",
        vehicle_model: owner.vehicle_model || "",
        entry_time: s.entry_time,
        exit_time: null,
        status: "inside",
        visit_date: s.visit_date,
        entry_image: s.entry_image,
        live: true,
      };
      return [row, ...(prev || []).filter((r) => r.id !== row.id)].slice(0, 8);
    });
    setStats((prev) => {
      if (!prev) return prev;
      const bump = (k) => ({ ...prev[k], value: (prev[k]?.value || 0) + 1 });
      return {
        ...prev,
        today_entries: bump("today_entries"),
        visitors_today: bump("visitors_today"),
        monthly_visitors: bump("monthly_visitors"),
        cars_inside: bump("cars_inside"),
        total_vehicles: msg.owner_status === "new" ? bump("total_vehicles") : prev.total_vehicles,
      };
    });
  });

  const firstName = (user?.name || "").split(" ")[0];
  const STATS = stats ? [
    { key: "today_entries", label: "Today's entries", icon: LogInIcon, accent: "blue" },
    { key: "today_exits", label: "Today's exits", icon: LogOut, accent: "sky" },
    { key: "cars_inside", label: "Cars inside", icon: Car, accent: "green" },
    { key: "visitors_today", label: "Visitors today", icon: UsersIcon, accent: "amber" },
    { key: "monthly_visitors", label: "This month", icon: CalendarRange, accent: "violet", compareLabel: "vs last month" },
    { key: "total_vehicles", label: "Total vehicles", icon: Database, accent: "blue", compareLabel: "growth" },
  ] : [];

  return (
    <div className="space-y-6" data-testid="dashboard-root">
      <PageHeader
        icon={LayoutDashboard}
        title={`${greeting()}${firstName ? `, ${firstName}` : ""}`}
        description={`Here's what's happening at the showroom today · ${new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })}`}
        actions={
          <Button variant="outline" onClick={() => load(true)} disabled={refreshing || loading} data-testid="dashboard-refresh">
            <RefreshCw className={refreshing ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Refresh
          </Button>
        }
      />

      {error && !stats ? (
        <div className="surface"><ErrorState onRetry={() => load()} /></div>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-6">
            {loading || !stats
              ? Array.from({ length: 6 }).map((_, i) => <CardSkeleton key={i} />)
              : STATS.map((s) => (
                <StatCard
                  key={s.key}
                  testId={`stat-${s.key.replace(/_/g, "-")}`}
                  label={s.label}
                  value={stats[s.key]?.value}
                  change={stats[s.key]?.change}
                  trend={stats[s.key]?.trend}
                  icon={s.icon}
                  accent={s.accent}
                  compareLabel={s.compareLabel}
                />
              ))}
          </div>

          <div className="grid gap-6 xl:grid-cols-3">
            <SectionCard
              className="xl:col-span-2"
              icon={Clock}
              title="Recent vehicles"
              description="Latest entries across all cameras"
              noPadding
              actions={
                <Button asChild variant="ghost" size="sm">
                  <Link to="/vehicles">View all <ArrowRight className="h-3.5 w-3.5" /></Link>
                </Button>
              }
              data-testid="recent-vehicles-card"
            >
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Vehicle</th>
                      <th>Owner</th>
                      <th>Entry</th>
                      <th>Duration</th>
                      <th>Status</th>
                      <th className="text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {loading ? (
                      <TableSkeletonRows rows={6} cols={6} />
                    ) : recent.length === 0 ? (
                      <tr className="hover:bg-transparent">
                        <td colSpan={6}>
                          <EmptyState icon={Car} title="No vehicles yet" description="Vehicle entries will show up here as soon as they're recorded." />
                        </td>
                      </tr>
                    ) : (
                      recent.map((v) => (
                        <tr key={v.id} className={v.live ? "bg-success/5" : ""} data-testid={`recent-row-${v.vehicle_number}`}>
                          <td><VehiclePlate number={v.vehicle_number} /></td>
                          <td>
                            <div className="font-medium">{v.owner_name}</div>
                            <div className="text-xs text-muted-foreground">{v.vehicle_model || "—"}</div>
                          </td>
                          <td className="whitespace-nowrap text-muted-foreground">{formatDateTime(v.entry_time)}</td>
                          <td className="whitespace-nowrap tabular-nums text-muted-foreground">{elapsed(v.entry_time, v.exit_time)}</td>
                          <td><StatusBadge status={v.status} /></td>
                          <td className="text-right">
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => { setSelected(v); setOpen(true); }}
                              data-testid={`view-vehicle-${v.vehicle_number}`}
                            >
                              <Eye className="h-3.5 w-3.5" /> View
                            </Button>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </SectionCard>

            <CamerasCard />
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            <LiveOccupancyChart liveValue={stats?.cars_inside?.value} />
            <AgentHealthCard />
          </div>
        </>
      )}

      <VehicleDetailsDialog vehicle={selected} open={open} onOpenChange={setOpen} />
    </div>
  );
}
