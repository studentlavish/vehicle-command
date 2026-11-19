import React, { useCallback, useEffect, useState } from "react";
import api, { assetUrl, downloadBlob, errorMessage } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import PageHeader from "@/components/common/PageHeader";
import StatusBadge from "@/components/common/StatusBadge";
import VehiclePlate from "@/components/common/VehiclePlate";
import { EmptyState, ErrorState } from "@/components/common/EmptyState";
import useLiveEvents from "@/hooks/useLiveEvents";
import { toast } from "sonner";
import { ScanEye, Check, X, Camera, Clock, AlertTriangle, Loader2, ImageOff, CheckCheck, Download } from "lucide-react";
import { formatDateTime, relativeAgo } from "@/lib/time";
import { cn } from "@/lib/utils";

const CONF_TONE = { high: "success", medium: "warning", low: "danger", confirmed: "primary" };

function ReviewCard({ review, onDone }) {
  const [plate, setPlate] = useState(review.plate || review.candidates?.[0] || "");
  const [busy, setBusy] = useState(null);
  const [nonstandard, setNonstandard] = useState(false);
  const noPlate = review.kind === "no_plate";
  const options = Array.from(new Set([...(review.suggestions || []), review.plate, ...(review.candidates || [])].filter(Boolean)));

  const accept = async (e) => {
    e?.preventDefault();
    if (!plate.trim()) return;
    setBusy("accept");
    try {
      const { data } = await api.post(`/plate-reviews/${review.id}/accept`, { plate: plate.trim(), nonstandard });
      if (data.duplicate) toast.info(`${data.plate} confirmed`, { description: data.duplicate_reason || "Already recorded — no new entry created." });
      else toast.success(`${data.plate} — ${data.event === "exit" ? "exit" : "entry"} recorded`);
      onDone(review.id);
    } catch (err) {
      toast.error("Couldn't confirm", { description: errorMessage(err) });
    } finally {
      setBusy(null);
    }
  };

  const reject = async () => {
    setBusy("reject");
    try {
      await api.post(`/plate-reviews/${review.id}/reject`);
      toast("Read discarded");
      onDone(review.id);
    } catch (err) {
      toast.error("Couldn't discard", { description: errorMessage(err) });
    } finally {
      setBusy(null);
    }
  };

  return (
    <form onSubmit={accept} className="surface overflow-hidden" data-testid={`review-${review.id}`}>
      <div className="grid gap-0 md:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <div className="relative bg-slate-900">
          {review.image ? (
            <img src={assetUrl(review.image)} alt="Vehicle" className="aspect-video w-full object-contain" />
          ) : (
            <div className="flex aspect-video items-center justify-center text-white/50"><ImageOff className="h-8 w-8" /></div>
          )}
          {review.plate_image && (
            <img src={assetUrl(review.plate_image)} alt="Plate close-up" className="absolute bottom-2 left-2 h-14 max-w-[60%] rounded-md border-2 border-white bg-white object-contain shadow-pop" />
          )}
        </div>

        <div className="flex flex-col gap-4 p-5">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1 font-mono"><Camera className="h-3.5 w-3.5" />{review.camera_id || "—"}</span>
            <span className="inline-flex items-center gap-1" title={formatDateTime(review.seen_at)}><Clock className="h-3.5 w-3.5" />{relativeAgo(review.seen_at)}</span>
            {noPlate
              ? <StatusBadge tone="warning">No readable plate</StatusBadge>
              : <StatusBadge tone={CONF_TONE[review.confidence] || "neutral"}>{review.confidence || "unknown"} confidence</StatusBadge>}
            {review.reads ? <span>{review.reads} read{review.reads === 1 ? "" : "s"}</span> : null}
          </div>

          <div className="flex items-start gap-2 rounded-lg bg-warning/10 px-3 py-2 text-sm text-warning">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{review.reason}{review.raw && review.raw !== review.plate ? <> · camera read <span className="font-mono font-semibold">{review.raw}</span></> : null}</span>
          </div>

          <div>
            <label className="field-label">
              {noPlate ? "Type the plate if you can see one (or the trade / temporary number)" : "Number plate — check against the photo"}
            </label>
            <Input
              value={plate}
              onChange={(e) => setPlate(e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, ""))}
              className="h-12 font-mono text-lg font-semibold tracking-widest"
              placeholder="e.g. KA05MG9999"
              data-testid="review-plate-input"
            />
            {options.length > 1 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {options.map((o) => (
                  <button
                    key={o}
                    type="button"
                    onClick={() => setPlate(o)}
                    className={cn(
                      "rounded-md border px-2 py-1 font-mono text-xs transition-colors",
                      plate === o ? "border-primary bg-primary/10 text-primary" : "hover:border-primary/50",
                    )}
                  >
                    {o}{(review.suggestions || []).includes(o) ? " · inside" : ""}
                  </button>
                ))}
              </div>
            )}
            <label className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
              <input type="checkbox" checked={nonstandard} onChange={(e) => setNonstandard(e.target.checked)} className="h-3.5 w-3.5 accent-primary" />
              Non-standard plate (trade, temporary or foreign) — save exactly as typed
            </label>
          </div>

          <div className="mt-auto flex gap-2">
            <Button type="submit" className="flex-1" disabled={!!busy || !plate} data-testid="review-accept">
              {busy === "accept" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />} Confirm
            </Button>
            <Button type="button" variant="outline" onClick={reject} disabled={!!busy} data-testid="review-reject">
              {busy === "reject" ? <Loader2 className="h-4 w-4 animate-spin" /> : <X className="h-4 w-4" />} {noPlate ? "Not a vehicle" : "Not a plate"}
            </Button>
          </div>
        </div>
      </div>
    </form>
  );
}

function ResolvedTable({ rows }) {
  if (!rows.length) return <EmptyState icon={CheckCheck} title="Nothing resolved yet" />;
  return (
    <div className="overflow-x-auto">
      <table className="data-table">
        <thead><tr><th>Seen</th><th>Camera read</th><th>Final plate</th><th>Result</th><th>By</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td className="whitespace-nowrap text-muted-foreground">{formatDateTime(r.seen_at)}</td>
              <td className="font-mono text-xs">{r.raw || "—"}</td>
              <td>{r.final_plate ? <VehiclePlate number={r.final_plate} /> : <span className="text-muted-foreground">—</span>}</td>
              <td>
                {r.status === "rejected"
                  ? <StatusBadge tone="neutral">Discarded</StatusBadge>
                  : <StatusBadge tone={r.duplicate ? "neutral" : "success"}>{r.duplicate ? "Already recorded" : r.result_event === "exit" ? "Exit" : "Entry"}</StatusBadge>}
              </td>
              <td className="text-xs text-muted-foreground">{r.resolved_by || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ExportLabels() {
  const [stats, setStats] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.get("/ocr-labels/stats").then(({ data }) => setStats(data)).catch(() => setStats(null));
  }, []);
  const download = async () => {
    setBusy(true);
    try {
      const res = await api.get("/ocr-labels/export", { responseType: "blob" });
      downloadBlob(res, `plate-labels-${new Date().toISOString().slice(0, 10)}.zip`);
    } catch (err) {
      toast.error("Export failed", { description: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Button variant="outline" onClick={download} disabled={busy || !stats?.total} title="Plate crops with the confirmed text — for measuring and retraining the reader">
      {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
      Export training data{stats ? ` (${stats.total})` : ""}
    </Button>
  );
}

export default function Reviews() {
  const { user } = useAuth();
  const [tab, setTab] = useState("pending");
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    setError(false);
    try {
      const { data } = await api.get("/plate-reviews", { params: { status: tab === "pending" ? "pending" : "all", limit: 200 } });
      setRows(tab === "pending" ? data : data.filter((r) => r.status !== "pending" && r.status !== "processing"));
    } catch {
      setError(true);
    }
  }, [tab]);

  useEffect(() => { setRows(null); load(); }, [load]);

  useLiveEvents((msg) => {
    if (msg.type === "review.created" && tab === "pending") {
      setRows((prev) => (prev ? [msg.review, ...prev.filter((r) => r.id !== msg.review.id)] : prev));
    }
  });

  const done = (id) => {
    setRows((prev) => prev.filter((r) => r.id !== id));
    window.dispatchEvent(new Event("reviews:changed"));
  };

  return (
    <div className="space-y-6" data-testid="reviews-page">
      <PageHeader
        icon={ScanEye}
        title="Plate review"
        description="Reads the system wasn't sure about. Check each one against the photo, correct it if needed, then confirm."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {user?.role === "admin" && <ExportLabels />}
            <Tabs value={tab} onValueChange={setTab}>
              <TabsList>
                <TabsTrigger value="pending">Needs review{tab === "pending" && rows ? ` (${rows.length})` : ""}</TabsTrigger>
                <TabsTrigger value="resolved">Resolved</TabsTrigger>
              </TabsList>
            </Tabs>
          </div>
        }
      />

      {error ? (
        <div className="surface"><ErrorState onRetry={load} /></div>
      ) : rows === null ? (
        <div className="space-y-4">{[0, 1].map((i) => <Skeleton key={i} className="h-64 rounded-xl" />)}</div>
      ) : tab === "pending" ? (
        rows.length === 0 ? (
          <div className="surface">
            <EmptyState icon={CheckCheck} title="All caught up" description="Every camera read has been confirmed. New uncertain reads will appear here automatically." />
          </div>
        ) : (
          <div className="space-y-4">{rows.map((r) => <ReviewCard key={r.id} review={r} onDone={done} />)}</div>
        )
      ) : (
        <div className="surface overflow-hidden"><ResolvedTable rows={rows} /></div>
      )}
    </div>
  );
}
