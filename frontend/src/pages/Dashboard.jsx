import React, { useEffect, useState } from "react";
import { motion } from "framer-motion";
import api from "@/lib/api";
import StatCard from "@/components/StatCard";
import VehicleDetailsDialog from "@/components/VehicleDetailsDialog";
import {
  LogIn as LogInIcon,
  LogOut,
  Car,
  Database,
  Users as UsersIcon,
  CalendarRange,
  Camera,
  RadioTower,
  Eye,
  Sparkles,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

function formatTime(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" }); } catch { return iso; }
}

const statusStyle = {
  inside: "bg-emerald-500/15 text-emerald-300 border-emerald-500/25",
  exited: "bg-slate-500/15 text-slate-300 border-slate-500/25",
  pending: "bg-amber-500/15 text-amber-300 border-amber-500/25",
};

export default function Dashboard() {
  const [stats, setStats] = useState(null);
  const [recent, setRecent] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const [s, v] = await Promise.all([
          api.get("/dashboard/stats"),
          api.get("/vehicles", { params: { limit: 8 } }),
        ]);
        setStats(s.data);
        setRecent(v.data);
      } catch (e) {
        if (process.env.NODE_ENV === "development") {
          console.error("Dashboard load error:", e);
        }
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  return (
    <div className="space-y-8" data-testid="dashboard-root">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-4">
        <div>
          <div className="inline-flex items-center gap-2 text-xs uppercase tracking-[0.25em] text-blue-400/80">
            <Sparkles className="h-3.5 w-3.5" /> Command Center
          </div>
          <h1 className="mt-2 text-4xl md:text-5xl font-bold tracking-tight">Dashboard</h1>
          <p className="mt-2 text-slate-400 text-sm">Real-time view of showroom activity, vehicle flow and monitoring.</p>
        </div>
        <div className="glass rounded-xl px-4 py-3 flex items-center gap-3">
          <div className="h-9 w-9 rounded-lg bg-blue-500/15 border border-blue-500/30 flex items-center justify-center">
            <CalendarRange className="h-4 w-4 text-blue-400" />
          </div>
          <div className="leading-tight">
            <div className="text-xs text-slate-400">Today</div>
            <div className="text-sm font-medium">{new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short", year: "numeric" })}</div>
          </div>
        </div>
      </div>

      {/* Stat cards */}
      {loading || !stats ? (
        <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={`stat-skel-${i}`} className="h-40 bg-white/5 rounded-2xl" />
          ))}
        </div>
      ) : (
        <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
          <StatCard testId="stat-today-entry" label="Today's Entry" value={stats.today_entries.value} change={stats.today_entries.change} trend={stats.today_entries.trend} icon={LogInIcon} accent="blue" delay={0} />
          <StatCard testId="stat-today-exit" label="Today's Exit" value={stats.today_exits.value} change={stats.today_exits.change} trend={stats.today_exits.trend} icon={LogOut} accent="sky" delay={0.05} />
          <StatCard testId="stat-cars-inside" label="Cars Inside" value={stats.cars_inside.value} change={stats.cars_inside.change} trend={stats.cars_inside.trend} icon={Car} accent="green" delay={0.1} />
          <StatCard testId="stat-total-vehicles" label="Total Vehicles" value={stats.total_vehicles.value} change={stats.total_vehicles.change} trend={stats.total_vehicles.trend} icon={Database} accent="violet" delay={0.15} />
          <StatCard testId="stat-visitors-today" label="Visitors Today" value={stats.visitors_today.value} change={stats.visitors_today.change} trend={stats.visitors_today.trend} icon={UsersIcon} accent="amber" delay={0.2} />
          <StatCard testId="stat-monthly-visitors" label="Monthly Visitors" value={stats.monthly_visitors.value} change={stats.monthly_visitors.change} trend={stats.monthly_visitors.trend} icon={CalendarRange} accent="blue" delay={0.25} />
        </div>
      )}

      {/* Live camera + Recent table */}
      <div className="grid gap-6 lg:grid-cols-3">
        {/* Live camera */}
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.1 }}
          className="glass rounded-2xl p-5 lg:col-span-1 relative overflow-hidden"
          data-testid="live-camera-card"
        >
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <RadioTower className="h-4 w-4 text-blue-400" />
              <h3 className="font-semibold">Live Camera</h3>
            </div>
            <Badge className="bg-emerald-500/15 text-emerald-300 border border-emerald-500/25">
              <span className="pulse-dot text-emerald-400 mr-1">Online</span>
            </Badge>
          </div>

          <div className="relative aspect-video rounded-xl overflow-hidden border border-white/10 bg-gradient-to-br from-slate-800 to-slate-900">
            <img
              src="https://images.unsplash.com/photo-1485291571150-772bcfc10da5?auto=format&fit=crop&w=900&q=60"
              className="w-full h-full object-cover opacity-60"
              alt="Live feed placeholder"
            />
            <div className="absolute inset-0 grid-bg opacity-40" />
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="glass rounded-full p-4">
                <Camera className="h-8 w-8 text-blue-400" />
              </div>
            </div>
            <div className="absolute top-3 left-3 flex items-center gap-2 text-xs">
              <span className="h-2 w-2 rounded-full bg-red-500 animate-pulse" />
              <span className="font-mono tracking-wider">REC • CAM 01</span>
            </div>
            <div className="absolute bottom-3 right-3 text-[10px] font-mono text-slate-300/80">
              {new Date().toLocaleTimeString()}
            </div>
          </div>

          <div className="mt-4 grid grid-cols-2 gap-3 text-xs">
            <div className="glass-strong rounded-lg p-3">
              <div className="text-slate-400">Camera Status</div>
              <div className="mt-1 text-emerald-300 font-medium">Online</div>
            </div>
            <div className="glass-strong rounded-lg p-3">
              <div className="text-slate-400">Last Scan</div>
              <div className="mt-1 font-mono">{new Date().toLocaleTimeString()}</div>
            </div>
          </div>

          <div className="mt-4 text-[11px] text-slate-500">
            AI Number Plate Recognition and full CCTV feed integration are prepared for phase-2 rollout.
          </div>
        </motion.div>

        {/* Recent Table */}
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.15 }}
          className="glass rounded-2xl p-5 lg:col-span-2 overflow-hidden"
          data-testid="recent-vehicles-card"
        >
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-semibold">Recent Vehicles</h3>
            <a href="/vehicles" className="text-xs text-blue-400 hover:text-blue-300">View all →</a>
          </div>

          {loading ? (
            <div className="space-y-2">
              {Array.from({ length: 6 }).map((_, i) => <Skeleton key={`recent-skel-${i}`} className="h-11 bg-white/5" />)}
            </div>
          ) : recent.length === 0 ? (
            <div className="text-center text-slate-400 py-10 text-sm">No vehicle records yet.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-slate-400 text-[11px] uppercase tracking-widest">
                    <th className="py-2 font-normal">Vehicle #</th>
                    <th className="py-2 font-normal">Owner</th>
                    <th className="py-2 font-normal">Entry</th>
                    <th className="py-2 font-normal">Status</th>
                    <th className="py-2 font-normal text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5">
                  {recent.map((v) => (
                    <tr key={v.id} className="hover:bg-white/[0.03] transition-colors" data-testid={`recent-row-${v.vehicle_number}`}>
                      <td className="py-3 font-mono text-white">{v.vehicle_number}</td>
                      <td className="py-3">
                        <div className="text-white">{v.owner_name}</div>
                        <div className="text-[11px] text-slate-500">{v.vehicle_model}</div>
                      </td>
                      <td className="py-3 text-slate-300">{formatTime(v.entry_time)}</td>
                      <td className="py-3">
                        <Badge className={`${statusStyle[v.status]} border capitalize`}>{v.status}</Badge>
                      </td>
                      <td className="py-3 text-right">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => { setSelected(v); setOpen(true); }}
                          className="h-8 text-blue-400 hover:text-white hover:bg-blue-600/20"
                          data-testid={`view-vehicle-${v.vehicle_number}`}
                        >
                          <Eye className="h-3.5 w-3.5 mr-1" />View
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </motion.div>
      </div>

      <VehicleDetailsDialog vehicle={selected} open={open} onOpenChange={setOpen} />
    </div>
  );
}
