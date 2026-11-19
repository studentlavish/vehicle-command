import React, { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import api from "@/lib/api";
import SectionCard from "@/components/common/SectionCard";
import { EmptyState, ErrorState } from "@/components/common/EmptyState";
import { Skeleton } from "@/components/ui/skeleton";
import { Hourglass, CheckCircle2 } from "lucide-react";
import { formatDateTime, relativeAgo } from "@/lib/time";

/** Cars recorded as inside for longer than the limit — a missed exit, or kept overnight. */
export default function LongOpenCard() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/visit-sessions/long-open");
      setData(data);
      setErr(false);
    } catch {
      setErr(true);
    }
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 5 * 60 * 1000);
    return () => clearInterval(t);
  }, [load]);

  const list = data?.sessions || [];
  return (
    <SectionCard
      icon={Hourglass}
      title="Long-open visits"
      description={`Inside for more than ${data?.hours ?? 24} hours — check whether the exit was missed`}
      actions={data ? <span className="text-xs font-medium text-muted-foreground">{list.length}</span> : null}
      data-testid="long-open-card"
    >
      {err && !data ? (
        <ErrorState onRetry={load} className="py-6" />
      ) : !data ? (
        <div className="space-y-2">{[0, 1].map((i) => <Skeleton key={i} className="h-10 w-full" />)}</div>
      ) : list.length === 0 ? (
        <EmptyState compact icon={CheckCircle2} title="Nothing unusual" description="Every car inside entered recently." />
      ) : (
        <ul className="divide-y">
          {list.slice(0, 8).map((s) => (
            <li key={s.id} className="flex items-center justify-between gap-3 py-2 text-sm">
              <Link to={`/vehicle/${encodeURIComponent(s.vehicle_number)}`} className="font-mono font-medium hover:underline">
                {s.vehicle_number}
              </Link>
              <span className="text-muted-foreground" title={formatDateTime(s.entry_time)}>entered {relativeAgo(s.entry_time)}</span>
            </li>
          ))}
          {list.length > 8 && <li className="py-2 text-xs text-muted-foreground">and {list.length - 8} more</li>}
        </ul>
      )}
    </SectionCard>
  );
}
