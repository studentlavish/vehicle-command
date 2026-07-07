import React, { useState } from "react";
import { motion } from "framer-motion";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import { FileBarChart, FileSpreadsheet, FileText, FileDown, Loader2, History } from "lucide-react";
import api from "@/lib/api";
import RangeFilters from "@/components/RangeFilters";

const PERIODS = [
  { key: "daily", label: "Daily Report", desc: "Vehicle activity in the last 24 hours." },
  { key: "weekly", label: "Weekly Report", desc: "Rolling 7-day summary of entries and exits." },
  { key: "monthly", label: "Monthly Report", desc: "Comprehensive 30-day activity log." },
];

const FORMATS = [
  { key: "xlsx", label: "Excel", icon: FileSpreadsheet, tone: "text-emerald-300 border-emerald-500/30 hover:bg-emerald-500/10" },
  { key: "pdf", label: "PDF", icon: FileText, tone: "text-rose-300 border-rose-500/30 hover:bg-rose-500/10" },
  { key: "csv", label: "CSV", icon: FileDown, tone: "text-sky-300 border-sky-500/30 hover:bg-sky-500/10" },
];

function downloadBlob(res, filename) {
  const blob = new Blob([res.data]);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export default function Reports() {
  const [busy, setBusy] = useState(null);

  const download = async (period, fmt) => {
    setBusy(`${period}-${fmt}`);
    try {
      const res = await api.get(`/reports/export`, { params: { period, format: fmt }, responseType: "blob" });
      downloadBlob(res, `rdx_report_${period}.${fmt}`);
      toast.success(`${fmt.toUpperCase()} report ready`);
    } catch (e) {
      toast.error("Export failed", { description: e.message });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-6" data-testid="reports-page">
      <div>
        <div className="inline-flex items-center gap-2 text-xs uppercase tracking-[0.25em] text-blue-400/80">
          <FileBarChart className="h-3.5 w-3.5" /> Exports
        </div>
        <h1 className="mt-2 text-4xl md:text-5xl font-bold tracking-tight">Reports</h1>
        <p className="text-slate-400 mt-2 text-sm">Generate and export detailed showroom reports on demand.</p>
      </div>

      <div className="grid gap-5 md:grid-cols-3">
        {PERIODS.map((p, idx) => (
          <motion.div
            key={p.key}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.35, delay: idx * 0.05 }}
            className="glass rounded-2xl p-6"
            data-testid={`report-card-${p.key}`}
          >
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-semibold">{p.label}</h3>
              <div className="h-10 w-10 rounded-lg bg-blue-500/15 border border-blue-500/30 flex items-center justify-center">
                <FileBarChart className="h-5 w-5 text-blue-400" />
              </div>
            </div>
            <p className="text-sm text-slate-400 mt-2">{p.desc}</p>

            <div className="mt-5 grid grid-cols-3 gap-2">
              {FORMATS.map((f) => {
                const key = `${p.key}-${f.key}`;
                const active = busy === key;
                return (
                  <Button
                    key={f.key}
                    onClick={() => download(p.key, f.key)}
                    disabled={active}
                    variant="outline"
                    className={`justify-center bg-white/5 border ${f.tone}`}
                    data-testid={`export-${p.key}-${f.key}`}
                  >
                    {active ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <f.icon className="h-4 w-4 mr-2" />}
                    {f.label}
                  </Button>
                );
              })}
            </div>
          </motion.div>
        ))}
      </div>

      <VisitHistoryExport />

      <div className="glass rounded-2xl p-6">
        <h3 className="font-semibold">WhatsApp / Email Delivery <span className="text-[10px] text-slate-500 uppercase tracking-widest ml-1">Phase 2</span></h3>
        <p className="text-sm text-slate-400 mt-2">
          Scheduled report delivery via Twilio WhatsApp + Resend Email is queued for the next release.
          Configure delivery times under Settings → WhatsApp Report Time.
        </p>
      </div>
    </div>
  );
}

function VisitHistoryExport() {
  const [range, setRange] = useState("30d");
  const [customFrom, setCustomFrom] = useState(null);
  const [customTo, setCustomTo] = useState(null);
  const [vehicleNumber, setVehicleNumber] = useState("");
  const [busy, setBusy] = useState(null);

  const doExport = async (fmt) => {
    setBusy(fmt);
    try {
      const params = { format: fmt, range };
      if (range === "custom") {
        if (customFrom) params.from_ = new Date(customFrom).toISOString().slice(0, 10);
        if (customTo) params.to = new Date(customTo).toISOString().slice(0, 10);
      }
      if (vehicleNumber.trim()) params.vehicle_number = vehicleNumber.trim();
      const res = await api.get("/visits/export", { params, responseType: "blob" });
      downloadBlob(res, `visit_history_${vehicleNumber.trim() || "all"}.${fmt}`);
      toast.success(`Visit history exported as ${fmt.toUpperCase()}`);
    } catch (e) {
      toast.error("Export failed", { description: e.message });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="glass rounded-2xl p-6" data-testid="visit-history-export">
      <div className="flex items-center gap-3 mb-4">
        <div className="h-10 w-10 rounded-lg bg-blue-500/15 border border-blue-500/30 flex items-center justify-center">
          <History className="h-5 w-5 text-blue-400" />
        </div>
        <div>
          <h3 className="font-semibold">Export Visit History</h3>
          <p className="text-sm text-slate-400">All visit sessions filtered by date range — optionally for a specific vehicle.</p>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <Label className="text-slate-300 text-xs uppercase tracking-widest">Vehicle Number (optional)</Label>
          <Input
            value={vehicleNumber}
            onChange={(e) => setVehicleNumber(e.target.value)}
            placeholder="Leave blank for all vehicles"
            className="mt-2 bg-white/5 border-white/10 text-white font-mono"
            data-testid="visit-history-vehicle-input"
          />
        </div>
        <div className="text-[11px] text-slate-500 self-end pb-2">
          Retention: last 180 days.
        </div>
      </div>

      <div className="mt-4">
        <RangeFilters
          value={range}
          onChange={setRange}
          customFrom={customFrom}
          customTo={customTo}
          onCustom={(f, t) => { setCustomFrom(f); setCustomTo(t); setRange("custom"); }}
        />
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {FORMATS.map((f) => (
          <Button
            key={f.key}
            onClick={() => doExport(f.key)}
            disabled={busy === f.key}
            variant="outline"
            className={`bg-white/5 border ${f.tone}`}
            data-testid={`visit-export-${f.key}`}
          >
            {busy === f.key ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <f.icon className="h-4 w-4 mr-2" />}
            {f.label}
          </Button>
        ))}
      </div>
    </div>
  );
}
