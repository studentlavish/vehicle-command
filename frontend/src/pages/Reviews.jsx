import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
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
import {
  ScanEye, Check, X, Camera, Clock, AlertTriangle, Loader2, ImageOff, CheckCheck, Download, Keyboard,
  Maximize2, List,
} from "lucide-react";
import { formatDateTime, relativeAgo } from "@/lib/time";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const CONF_TONE = { high: "success", medium: "warning", low: "danger", confirmed: "primary" };

const ReviewCard = forwardRef(function ReviewCard({ review, onDone, active = false, gate = false, onFocus }, ref) {
  const { t } = useT();
  const [plate, setPlate] = useState(review.plate || review.candidates?.[0] || "");
  const [busy, setBusy] = useState(null);
  const [nonstandard, setNonstandard] = useState(false);
  const inputRef = useRef(null);
  const noPlate = review.kind === "no_plate";
  const options = Array.from(new Set([...(review.suggestions || []), review.plate, ...(review.candidates || [])].filter(Boolean)));

  const accept = async (e) => {
    e?.preventDefault();
    if (!plate.trim() || busy) return;
    setBusy("accept");
    try {
      const { data } = await api.post(`/plate-reviews/${review.id}/accept`, { plate: plate.trim(), nonstandard });
      if (data.duplicate) toast.info(t("rv.already", { p: data.plate }), { description: data.duplicate_reason || t("rv.alreadyBody") });
      else toast.success(t("rv.confirmed", { p: data.plate, e: data.event === "exit" ? t("rv.exit") : t("rv.entry") }));
      onDone(review.id);
    } catch (err) {
      toast.error(t("rv.cantConfirm"), { description: errorMessage(err) });
    } finally {
      setBusy(null);
    }
  };

  const reject = async () => {
    if (busy) return;
    setBusy("reject");
    try {
      await api.post(`/plate-reviews/${review.id}/reject`);
      toast(t("rv.discarded"));
      onDone(review.id);
    } catch (err) {
      toast.error(t("rv.cantDiscard"), { description: errorMessage(err) });
    } finally {
      setBusy(null);
    }
  };

  useImperativeHandle(ref, () => ({
    confirm: () => accept(),
    reject,
    pick: (i) => { if (options[i]) setPlate(options[i]); },
    focusInput: () => inputRef.current?.focus(),
  }));

  useEffect(() => { if (gate && active) inputRef.current?.focus({ preventScroll: true }); }, [gate, active, review.id]);

  return (
    <form
      onSubmit={accept}
      onFocusCapture={onFocus}
      onMouseDown={onFocus}
      className={cn("surface overflow-hidden transition-shadow", active && !gate && "ring-2 ring-primary/50")}
      data-testid={`review-${review.id}`}
    >
      <div className={cn("grid gap-0", gate ? "lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]" : "md:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]")}>
        <div className="relative bg-slate-900">
          {review.image ? (
            <img src={assetUrl(review.image)} alt="Vehicle" className={cn("w-full object-contain", gate ? "aspect-[4/3] lg:h-full lg:max-h-[70vh]" : "aspect-video")} />
          ) : (
            <div className={cn("flex items-center justify-center text-white/50", gate ? "aspect-[4/3]" : "aspect-video")}><ImageOff className="h-8 w-8" /></div>
          )}
          {review.plate_image && (
            <img src={assetUrl(review.plate_image)} alt="Plate close-up"
                 className={cn("absolute bottom-2 left-2 max-w-[60%] rounded-md border-2 border-white bg-white object-contain shadow-pop", gate ? "h-24" : "h-14")} />
          )}
        </div>

        <div className={cn("flex flex-col gap-4", gate ? "p-6" : "p-5")}>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1 font-mono"><Camera className="h-3.5 w-3.5" />{review.camera_id || "—"}</span>
            <span className="inline-flex items-center gap-1" title={formatDateTime(review.seen_at)}><Clock className="h-3.5 w-3.5" />{relativeAgo(review.seen_at)}</span>
            {noPlate
              ? <StatusBadge tone="warning">{t("rv.noPlate")}</StatusBadge>
              : <StatusBadge tone={CONF_TONE[review.confidence] || "neutral"}>{t("rv.confidence", { c: review.confidence || "unknown" })}</StatusBadge>}
            {review.reads ? <span>{t("rv.reads", { n: review.reads })}</span> : null}
          </div>

          <div className="flex items-start gap-2 rounded-lg bg-warning/10 px-3 py-2 text-sm text-warning">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{review.reason}{review.raw && review.raw !== review.plate ? <> · {t("rv.cameraRead")} <span className="font-mono font-semibold">{review.raw}</span></> : null}</span>
          </div>

          <div>
            <label className="field-label">{noPlate ? t("rv.labelNoPlate") : t("rv.labelPlate")}</label>
            <Input
              ref={inputRef}
              value={plate}
              onChange={(e) => setPlate(e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, ""))}
              onKeyDown={(e) => { if (e.key === "Escape") e.currentTarget.blur(); }}
              className={cn("font-mono font-semibold tracking-widest", gate ? "h-16 text-3xl md:text-3xl" : "h-12 text-lg md:text-lg")}
              placeholder="e.g. KA05MG9999"
              data-testid="review-plate-input"
            />
            {options.length > 0 && (options.length > 1 || options[0] !== plate) && (
              <div className="mt-3">
                <div className="mb-1.5 text-[11px] text-muted-foreground">{t("rv.guesses")}</div>
                <div className="flex flex-wrap gap-1.5">
                  {options.map((o, i) => (
                    <button
                      key={o}
                      type="button"
                      onClick={() => setPlate(o)}
                      data-testid={`review-option-${i + 1}`}
                      className={cn(
                        "inline-flex items-center gap-1.5 rounded-md border font-mono transition-colors",
                        gate ? "px-3 py-2 text-base" : "px-2 py-1 text-xs",
                        plate === o ? "border-primary bg-primary/10 text-primary" : "hover:border-primary/50",
                      )}
                    >
                      {i < 3 && (
                        <kbd className={cn("rounded bg-muted px-1 font-sans font-semibold text-muted-foreground", gate ? "text-xs" : "text-[10px]")}>{i + 1}</kbd>
                      )}
                      {o}{(review.suggestions || []).includes(o) ? ` · ${t("rv.inside")}` : ""}
                    </button>
                  ))}
                </div>
              </div>
            )}
            <label className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
              <input type="checkbox" checked={nonstandard} onChange={(e) => setNonstandard(e.target.checked)} className="h-3.5 w-3.5 accent-primary" />
              {t("rv.nonstandard")}
            </label>
          </div>

          <div className={cn("mt-auto flex gap-2", gate && "flex-col")}>
            <Button type="submit" className={cn("flex-1", gate && "h-16 w-full flex-none text-lg")} disabled={!!busy || !plate} data-testid="review-accept">
              {busy === "accept" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className={gate ? "h-6 w-6" : "h-4 w-4"} />} {t("rv.confirm")}
              {active && <kbd className="ml-1 hidden rounded bg-white/20 px-1.5 text-[10px] font-normal sm:inline">Enter</kbd>}
            </Button>
            <Button type="button" variant="outline" onClick={reject} disabled={!!busy} className={cn(gate && "h-14 w-full text-lg")} data-testid="review-reject">
              {busy === "reject" ? <Loader2 className="h-4 w-4 animate-spin" /> : <X className={gate ? "h-6 w-6" : "h-4 w-4"} />} {noPlate ? t("rv.notVehicle") : t("rv.notPlate")}
              {active && <kbd className="ml-1 hidden rounded bg-muted px-1.5 text-[10px] font-normal sm:inline">R</kbd>}
            </Button>
          </div>
        </div>
      </div>
    </form>
  );
});

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

const isTyping = (el) => el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);

export default function Reviews() {
  const { user } = useAuth();
  const { t } = useT();
  const [params, setParams] = useSearchParams();
  const gate = params.get("view") === "gate";
  const [tab, setTab] = useState("pending");
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(false);
  const [active, setActive] = useState(0);
  const cardRefs = useRef({});

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
      // New reads join the end in gate view (so the one being handled doesn't jump), the top in list view.
      setRows((prev) => {
        if (!prev) return prev;
        const rest = prev.filter((r) => r.id !== msg.review.id);
        return gate ? [...rest, msg.review] : [msg.review, ...rest];
      });
      if (!gate) setActive((i) => (i > 0 ? i + 1 : i));
    }
  });

  const done = (id) => {
    setRows((prev) => {
      const next = prev.filter((r) => r.id !== id);
      setActive((i) => Math.min(i, Math.max(0, next.length - 1)));
      return next;
    });
    window.dispatchEvent(new Event("reviews:changed"));
  };

  const pending = tab === "pending" ? rows || [] : [];
  const current = pending[Math.min(active, Math.max(0, pending.length - 1))];

  // Keyboard shortcuts act on the highlighted card (gate view: the one shown).
  useEffect(() => {
    if (tab !== "pending") return undefined;
    const onKey = (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey || !current) return;
      if (isTyping(e.target)) return; // Enter inside the plate box submits its own form
      const card = cardRefs.current[current.id];
      if (!card) return;
      const k = e.key.toLowerCase();
      if (e.key === "Enter") { e.preventDefault(); card.confirm(); }
      else if (k === "r") { e.preventDefault(); card.reject(); }
      else if (["1", "2", "3"].includes(k)) { e.preventDefault(); card.pick(Number(k) - 1); }
      else if (k === "e" || k === "/") { e.preventDefault(); card.focusInput(); }
      else if (k === "j" || e.key === "ArrowDown") { e.preventDefault(); setActive((i) => Math.min(i + 1, pending.length - 1)); }
      else if (k === "k" || e.key === "ArrowUp") { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [tab, current, pending.length]);

  useEffect(() => {
    if (gate || !current) return;
    document.querySelector(`[data-testid="review-${current.id}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [gate, current]);

  const setGate = (on) => {
    const next = new URLSearchParams(params);
    if (on) next.set("view", "gate"); else next.delete("view");
    setParams(next, { replace: true });
    setTab("pending");
    setActive(0);
  };

  const card = (r, i, isGate = false) => (
    <ReviewCard
      key={r.id}
      ref={(el) => { if (el) cardRefs.current[r.id] = el; else delete cardRefs.current[r.id]; }}
      review={r}
      onDone={done}
      gate={isGate}
      active={r.id === current?.id}
      onFocus={() => setActive(i)}
    />
  );

  return (
    <div className={cn("space-y-6", gate && "space-y-4")} data-testid="reviews-page">
      <PageHeader
        icon={ScanEye}
        title={t("rv.title")}
        description={gate ? null : t("rv.desc")}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {!gate && user?.role === "admin" && <ExportLabels />}
            <Button variant={gate ? "default" : "outline"} onClick={() => setGate(!gate)} data-testid="review-gate-toggle">
              {gate ? <List className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />} {gate ? t("rv.list") : t("rv.gate")}
            </Button>
            {!gate && (
              <Tabs value={tab} onValueChange={(v) => { setTab(v); setActive(0); }}>
                <TabsList>
                  <TabsTrigger value="pending">{t("rv.pending")}{tab === "pending" && rows ? ` (${rows.length})` : ""}</TabsTrigger>
                  <TabsTrigger value="resolved">{t("rv.resolved")}</TabsTrigger>
                </TabsList>
              </Tabs>
            )}
          </div>
        }
      />

      {tab === "pending" && rows && rows.length > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
          <span className="hidden items-center gap-1.5 md:inline-flex"><Keyboard className="h-3.5 w-3.5" /> {t("rv.shortcuts")}</span>
          {gate && <span className="rounded-full bg-warning/10 px-2.5 py-1 font-semibold text-warning" data-testid="review-left">{t("rv.left", { n: rows.length })}</span>}
        </div>
      )}

      {error ? (
        <div className="surface"><ErrorState onRetry={load} /></div>
      ) : rows === null ? (
        <div className="space-y-4">{[0, 1].map((i) => <Skeleton key={i} className="h-64 rounded-xl" />)}</div>
      ) : tab === "pending" ? (
        rows.length === 0 ? (
          <div className="surface">
            <EmptyState icon={CheckCheck} title={t("rv.allCaught")} description={t("rv.allCaughtBody")} />
          </div>
        ) : gate ? (
          card(current, pending.indexOf(current), true)
        ) : (
          <div className="space-y-4">{rows.map((r, i) => card(r, i))}</div>
        )
      ) : (
        <div className="surface overflow-hidden"><ResolvedTable rows={rows} /></div>
      )}
    </div>
  );
}
