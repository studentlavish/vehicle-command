import React, { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { FileBarChart, Loader2, History, CalendarDays, CalendarRange, Calendar, MessageSquare } from "lucide-react";
import api, { downloadBlob, errorMessage } from "@/lib/api";
import RangeFilters from "@/components/RangeFilters";
import PageHeader from "@/components/common/PageHeader";
import SectionCard from "@/components/common/SectionCard";
import { toISODate } from "@/lib/time";
import { FORMATS } from "@/lib/exports";

const PERIODS = [
  { key: "daily", label: "Daily report", desc: "Vehicle activity in the last 24 hours.", icon: CalendarDays },
  { key: "weekly", label: "Weekly report", desc: "Rolling 7-day summary of entries and exits.", icon: CalendarRange },
  { key: "monthly", label: "Monthly report", desc: "Full 30-day activity log.", icon: Calendar },
];


function FormatButtons({ busyKey, prefix, onPick, testPrefix, size = "default" }) {
  return FORMATS.map((f) => {
    const active = busyKey === `${prefix}-${f.key}`;
    return (
      <Button
        key={f.key}
        size={size}
        variant="outline"
        onClick={() => onPick(f.key)}
        disabled={!!busyKey}
        data-testid={`${testPrefix}-${f.key}`}
      >
        {active ? <Loader2 className="h-4 w-4 animate-spin" /> : <f.icon className={`h-4 w-4 ${f.className}`} />}
        {f.label}
      </Button>
    );
  });
}

export default function Reports() {
  const [busy, setBusy] = useState(null);

  const download = async (period, fmt) => {
    setBusy(`${period}-${fmt}`);
    try {
      const res = await api.get("/reports/export", { params: { period, format: fmt }, responseType: "blob" });
      downloadBlob(res, `vashu_${period}_report_${toISODate(new Date())}.${fmt}`);
      toast.success("Report downloaded", { description: `${period[0].toUpperCase() + period.slice(1)} report · ${fmt.toUpperCase()}` });
    } catch (e) {
      toast.error("Export failed", { description: errorMessage(e) });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-6" data-testid="reports-page">
      <PageHeader icon={FileBarChart} title="Reports" description="Generate and download showroom activity reports." />

      <div className="grid gap-4 md:grid-cols-3">
        {PERIODS.map((p) => (
          <div key={p.key} className="surface flex flex-col p-5" data-testid={`report-card-${p.key}`}>
            <div className="flex items-start gap-3">
              <div className="icon-tile"><p.icon className="h-5 w-5" /></div>
              <div>
                <h3 className="font-semibold">{p.label}</h3>
                <p className="mt-0.5 text-sm text-muted-foreground">{p.desc}</p>
              </div>
            </div>
            <div className="mt-5 grid grid-cols-3 gap-2">
              <FormatButtons busyKey={busy} prefix={p.key} size="sm" testPrefix={`export-${p.key}`} onPick={(fmt) => download(p.key, fmt)} />
            </div>
          </div>
        ))}
      </div>

      <VisitHistoryExport />

      <SectionCard icon={MessageSquare} title="Scheduled delivery" description="WhatsApp and email delivery of daily reports">
        <p className="text-sm text-muted-foreground">
          Daily digests are sent automatically when WhatsApp (Twilio / Meta) or email keys are configured on the server.
          Set the delivery time under <span className="font-medium text-foreground">Settings → WhatsApp report</span>.
        </p>
      </SectionCard>
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
    if (range === "custom" && (!customFrom || !customTo)) {
      toast.error("Pick both a start and end date");
      return;
    }
    setBusy(`visit-${fmt}`);
    try {
      const params = { format: fmt, range };
      if (range === "custom") { params.from_ = toISODate(customFrom); params.to = toISODate(customTo); }
      if (vehicleNumber.trim()) params.vehicle_number = vehicleNumber.trim();
      const res = await api.get("/visits/export", { params, responseType: "blob" });
      downloadBlob(res, `visit_history_${vehicleNumber.trim() || "all"}.${fmt}`);
      toast.success("Visit history downloaded", { description: fmt.toUpperCase() });
    } catch (e) {
      toast.error("Export failed", { description: errorMessage(e) });
    } finally {
      setBusy(null);
    }
  };

  return (
    <SectionCard
      icon={History}
      title="Export visit history"
      description="Every visit in a date range — for all vehicles or a single one"
      data-testid="visit-history-export"
    >
      <div className="space-y-4">
        <div className="grid gap-4 md:grid-cols-[minmax(0,320px)_1fr] md:items-end">
          <div>
            <label className="field-label">Vehicle number <span className="font-normal text-muted-foreground">(optional)</span></label>
            <Input
              value={vehicleNumber}
              onChange={(e) => setVehicleNumber(e.target.value.toUpperCase())}
              placeholder="All vehicles"
              className="font-mono uppercase"
              data-testid="visit-history-vehicle-input"
            />
          </div>
          <RangeFilters
            value={range}
            onChange={setRange}
            customFrom={customFrom}
            customTo={customTo}
            onCustom={(f, t) => { setCustomFrom(f); setCustomTo(t); setRange("custom"); }}
          />
        </div>
        <div className="flex flex-wrap items-center gap-2 border-t pt-4">
          <span className="mr-1 text-sm text-muted-foreground">Download as</span>
          <FormatButtons busyKey={busy} prefix="visit" testPrefix="visit-export" onPick={doExport} />
        </div>
      </div>
    </SectionCard>
  );
}
