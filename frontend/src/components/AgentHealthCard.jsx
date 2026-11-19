import React, { useCallback, useEffect, useState } from "react";
import api from "@/lib/api";
import { Cpu, Camera, Wifi, WifiOff } from "lucide-react";
import SectionCard from "@/components/common/SectionCard";
import StatusBadge from "@/components/common/StatusBadge";
import { EmptyState, ErrorState } from "@/components/common/EmptyState";
import { Skeleton } from "@/components/ui/skeleton";
import { relativeAgo } from "@/lib/time";

/** Which showroom camera agents are online, last heartbeat and camera count. */
export default function AgentHealthCard() {
  const [agents, setAgents] = useState(null);
  const [err, setErr] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/agent/status");
      setAgents(Array.isArray(data) ? data : []);
      setErr(false);
    } catch {
      setErr(true);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [load]);

  const list = agents || [];
  const onlineCount = list.filter((a) => a.status === "online").length;

  return (
    <SectionCard
      icon={Cpu}
      title="Camera agents"
      description="Showroom PCs streaming camera feeds"
      actions={agents ? <span className="text-xs font-medium text-muted-foreground" data-testid="agent-count">{onlineCount}/{list.length} online</span> : null}
      data-testid="agent-health-card"
    >
      {err && !agents ? (
        <ErrorState onRetry={load} className="py-6" />
      ) : agents === null ? (
        <div className="space-y-2">{[0, 1].map((i) => <Skeleton key={i} className="h-14 w-full" />)}</div>
      ) : list.length === 0 ? (
        <EmptyState
          compact
          icon={WifiOff}
          title="No agents connected"
          description="Run the local camera agent on a showroom PC to start streaming."
          data-testid="agent-empty"
        />
      ) : (
        <div className="space-y-2" data-testid="agent-list">
          {list.map((a) => {
            const online = a.status === "online";
            const cams = (a.cameras || []).length;
            return (
              <div key={a.agent_id} className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2.5" data-testid={`agent-row-${a.agent_id}`}>
                <div className="flex min-w-0 items-center gap-3">
                  <div className={online ? "icon-tile-sm bg-success/10 text-success" : "icon-tile-sm bg-muted text-muted-foreground"}>
                    {online ? <Wifi className="h-4 w-4" /> : <WifiOff className="h-4 w-4" />}
                  </div>
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium">{a.hostname || a.agent_id}</div>
                    <div className="flex items-center gap-3 text-xs text-muted-foreground">
                      <span className="inline-flex items-center gap-1"><Camera className="h-3 w-3" />{cams} camera{cams === 1 ? "" : "s"}</span>
                      <span>Heartbeat {relativeAgo(a.last_heartbeat)}</span>
                    </div>
                  </div>
                </div>
                <StatusBadge status={online ? "online" : "offline"} />
              </div>
            );
          })}
        </div>
      )}
    </SectionCard>
  );
}
