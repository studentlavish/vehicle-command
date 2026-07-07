import React, { useEffect, useState } from "react";
import api from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { LogIn as LogInIcon, LogOut } from "lucide-react";

function formatTime(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }); } catch { return iso; }
}

export default function HistoryPage({ mode = "entry" }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const { data } = await api.get("/vehicles", { params: { limit: 500 } });
        // filter based on mode
        let filtered = data;
        if (mode === "entry") filtered = data.filter((v) => !!v.entry_time);
        if (mode === "exit") filtered = data.filter((v) => !!v.exit_time);
        filtered.sort((a, b) => {
          const ta = new Date(mode === "exit" ? a.exit_time : a.entry_time).getTime();
          const tb = new Date(mode === "exit" ? b.exit_time : b.entry_time).getTime();
          return tb - ta;
        });
        setRows(filtered);
      } finally {
        setLoading(false);
      }
    })();
  }, [mode]);

  const Icon = mode === "exit" ? LogOut : LogInIcon;
  const title = mode === "exit" ? "Exit History" : "Entry History";
  const subtitle = mode === "exit"
    ? "Chronological log of every vehicle exit from the showroom."
    : "Chronological log of every vehicle entry into the showroom.";

  return (
    <div className="space-y-6" data-testid={`${mode}-history-page`}>
      <div>
        <div className="inline-flex items-center gap-2 text-xs uppercase tracking-[0.25em] text-blue-400/80">
          <Icon className="h-3.5 w-3.5" /> {mode === "exit" ? "Log Out" : "Log In"}
        </div>
        <h1 className="mt-2 text-4xl md:text-5xl font-bold tracking-tight">{title}</h1>
        <p className="text-slate-400 mt-2 text-sm">{subtitle}</p>
      </div>

      <div className="glass rounded-2xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-slate-400 text-[11px] uppercase tracking-widest border-b border-white/5">
                <th className="px-5 py-3 font-normal">Vehicle #</th>
                <th className="px-5 py-3 font-normal">Owner</th>
                <th className="px-5 py-3 font-normal">Contact</th>
                <th className="px-5 py-3 font-normal">{mode === "exit" ? "Exit Time" : "Entry Time"}</th>
                <th className="px-5 py-3 font-normal">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {loading ? (
                Array.from({ length: 8 }).map((_, i) => (
                  <tr key={`h-skel-${i}`}><td colSpan={5} className="p-3"><Skeleton className="h-8 bg-white/5" /></td></tr>
                ))
              ) : rows.length === 0 ? (
                <tr><td colSpan={5} className="text-center py-14 text-slate-400">No records yet.</td></tr>
              ) : (
                rows.map((v) => (
                  <tr key={v.id} className="hover:bg-white/[0.03]">
                    <td className="px-5 py-3 font-mono text-white">{v.vehicle_number}</td>
                    <td className="px-5 py-3">{v.owner_name}<div className="text-[11px] text-slate-500">{v.vehicle_model}</div></td>
                    <td className="px-5 py-3 text-slate-300">{v.contact_number || "—"}</td>
                    <td className="px-5 py-3 text-slate-300">{formatTime(mode === "exit" ? v.exit_time : v.entry_time)}</td>
                    <td className="px-5 py-3">
                      <Badge className="border capitalize bg-white/5 text-slate-200 border-white/10">{v.status}</Badge>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
