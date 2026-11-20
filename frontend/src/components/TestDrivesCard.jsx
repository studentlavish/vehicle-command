import React, { useCallback, useEffect, useState } from "react";
import api, { errorMessage } from "@/lib/api";
import SectionCard from "@/components/common/SectionCard";
import { EmptyState, ErrorState } from "@/components/common/EmptyState";
import useLiveEvents from "@/hooks/useLiveEvents";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { toast } from "sonner";
import { CarFront, Timer, UserRound, CheckCircle2, AlertTriangle, Loader2 } from "lucide-react";
import { formatTime } from "@/lib/time";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

function minutesSince(iso) {
  const ms = Date.now() - new Date(iso).getTime();
  return Number.isNaN(ms) ? 0 : Math.max(0, Math.floor(ms / 60000));
}

function DriverForm({ trip, onSaved }) {
  const { t } = useT();
  const [driver, setDriver] = useState(trip.driver || "");
  const [customer, setCustomer] = useState(trip.customer || "");
  const [busy, setBusy] = useState(false);
  const save = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const { data } = await api.put(`/test-drives/${encodeURIComponent(trip.plate)}/trip`, { driver, customer });
      toast.success(t("td.saved"));
      onSaved(data);
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={save} className="space-y-2">
      <div className="text-sm font-medium">{t("td.who")} <span className="plate ml-1">{trip.plate}</span></div>
      <Input value={driver} onChange={(e) => setDriver(e.target.value)} placeholder={t("td.driver")} maxLength={80} autoFocus data-testid="td-driver" />
      <Input value={customer} onChange={(e) => setCustomer(e.target.value)} placeholder={t("td.customer")} maxLength={80} data-testid="td-customer" />
      <Button type="submit" size="sm" className="w-full" disabled={busy}>
        {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />} {t("td.save")}
      </Button>
    </form>
  );
}

/** Test-drive cars that are out (with a running timer and who is driving) and those back at the showroom. */
export default function TestDrivesCard() {
  const { t } = useT();
  const [data, setData] = useState(null);
  const [err, setErr] = useState(false);
  const [, tick] = useState(0);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/test-drives");
      setData(data);
      setErr(false);
    } catch {
      setErr(true);
    }
  }, []);

  useEffect(() => {
    load();
    const poll = setInterval(load, 60000);
    const clock = setInterval(() => tick((n) => n + 1), 30000);
    return () => { clearInterval(poll); clearInterval(clock); };
  }, [load]);

  // A test-drive car passing the gate changes its state — refresh right away.
  useLiveEvents((msg) => {
    if ((msg.type === "entry.recorded" || msg.type === "exit.recorded") && data) {
      const known = [...data.out, ...data.in, ...(data.unknown || [])].some((c) => c.plate === msg.vehicle_number);
      if (known) load();
    }
  });

  const max = data?.max_minutes ?? 60;
  const total = data ? data.out.length + data.in.length + (data.unknown?.length || 0) : 0;
  const saved = (trip) => setData((d) => ({ ...d, out: d.out.map((o) => (o.plate === trip.plate ? { ...o, ...trip } : o)) }));

  return (
    <SectionCard
      icon={CarFront}
      title={t("td.title")}
      description={t("td.desc")}
      actions={data ? <span className="text-xs font-medium text-muted-foreground">{data.out.length} / {total}</span> : null}
      data-testid="test-drives-card"
    >
      {err && !data ? (
        <ErrorState onRetry={load} className="py-6" />
      ) : !data ? (
        <div className="space-y-2">{[0, 1].map((i) => <Skeleton key={i} className="h-12 w-full" />)}</div>
      ) : total === 0 ? (
        <EmptyState compact icon={CarFront} title={t("td.none")} description={t("td.noneBody")} />
      ) : (
        <div className="space-y-4">
          <div>
            <div className="eyebrow mb-2">{t("td.out")}</div>
            {data.out.length === 0 ? (
              <div className="flex items-center gap-2 rounded-lg border border-dashed px-3 py-2.5 text-sm text-muted-foreground">
                <CheckCircle2 className="h-4 w-4 text-success" /> {t("td.allIn")}
              </div>
            ) : (
              <ul className="space-y-2">
                {data.out.map((o) => {
                  const mins = minutesSince(o.out_at);
                  const overdue = mins > max;
                  return (
                    <li key={o.plate} className={cn("flex items-center gap-3 rounded-lg border px-3 py-2.5",
                      overdue && "border-destructive/40 bg-destructive/5")} data-testid={`td-out-${o.plate}`}>
                      <div className={cn("icon-tile-sm", overdue ? "bg-destructive/10 text-destructive" : "bg-warning/10 text-warning")}>
                        {overdue ? <AlertTriangle className="h-4 w-4" /> : <Timer className="h-4 w-4" />}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="plate">{o.plate}</span>
                          {o.label && <span className="truncate text-xs text-muted-foreground">{o.label}</span>}
                        </div>
                        <div className="mt-0.5 truncate text-xs text-muted-foreground">
                          {formatTime(o.out_at)} · {o.driver || o.customer
                            ? [o.driver, o.customer].filter(Boolean).join(" · ")
                            : <span className="italic">{t("td.who")}</span>}
                        </div>
                      </div>
                      <div className={cn("text-right text-sm font-semibold tabular-nums", overdue ? "text-destructive" : "text-foreground")}>
                        {t("td.outFor", { m: mins })}
                        {overdue && <div className="text-[10px] font-medium uppercase">{t("td.overdue", { m: max })}</div>}
                      </div>
                      <Popover>
                        <PopoverTrigger asChild>
                          <Button size="icon" variant="ghost" className="h-8 w-8 shrink-0" aria-label={t("td.who")} data-testid={`td-edit-${o.plate}`}>
                            <UserRound className="h-4 w-4" />
                          </Button>
                        </PopoverTrigger>
                        <PopoverContent align="end" className="w-64">
                          <DriverForm trip={o} onSaved={saved} />
                        </PopoverContent>
                      </Popover>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {data.in.length > 0 && (
            <div>
              <div className="eyebrow mb-2">{t("td.in")}</div>
              <div className="flex flex-wrap gap-1.5">
                {data.in.map((c) => (
                  <span key={c.plate} className="plate" title={c.label || undefined}>{c.plate}</span>
                ))}
              </div>
            </div>
          )}

          {data.today?.length > 0 && (
            <div>
              <div className="eyebrow mb-2">{t("td.today")}</div>
              <ul className="divide-y text-sm">
                {data.today.slice(0, 6).map((trip) => (
                  <li key={`${trip.plate}-${trip.out_at}`} className="flex items-center justify-between gap-2 py-1.5">
                    <span className="font-mono text-xs font-semibold">{trip.plate}</span>
                    <span className="truncate text-xs text-muted-foreground">
                      {formatTime(trip.out_at)}–{formatTime(trip.back_at)}{trip.driver ? ` · ${trip.driver}` : ""}
                    </span>
                    <span className={cn("text-xs font-medium tabular-nums", trip.minutes > max && "text-destructive")}>
                      {trip.minutes != null ? t("td.min", { m: trip.minutes }) : "—"}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </SectionCard>
  );
}
