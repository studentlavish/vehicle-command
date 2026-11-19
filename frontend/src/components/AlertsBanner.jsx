import React, { useCallback, useEffect, useState } from "react";
import api from "@/lib/api";
import useLiveEvents from "@/hooks/useLiveEvents";
import { AlertOctagon, AlertTriangle } from "lucide-react";
import { relativeAgo } from "@/lib/time";
import { cn } from "@/lib/utils";

/** Problems the system has detected (camera offline, backup failed …) — shown on every page. */
export default function AlertsBanner() {
  const [firing, setFiring] = useState([]);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/alerts");
      setFiring(data?.firing || []);
    } catch {
      setFiring([]);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, [load]);
  useLiveEvents((msg) => { if (msg.type === "alert.changed") load(); });

  if (!firing.length) return null;
  return (
    <div className="mb-6 space-y-2" role="alert" data-testid="alerts-banner">
      {firing.map((a) => {
        const critical = a.severity === "critical";
        const Icon = critical ? AlertOctagon : AlertTriangle;
        return (
          <div
            key={a.key}
            className={cn(
              "flex items-start gap-2 rounded-lg border px-3 py-2 text-sm",
              critical ? "border-destructive/30 bg-destructive/10 text-destructive" : "border-warning/30 bg-warning/10 text-warning",
            )}
          >
            <Icon className="mt-0.5 h-4 w-4 shrink-0" />
            <span className="flex-1">{a.message}</span>
            <span className="shrink-0 text-xs opacity-75">since {relativeAgo(a.first_at)}</span>
          </div>
        );
      })}
    </div>
  );
}
