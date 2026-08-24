import React, { useEffect, useState } from "react";
import { motion } from "framer-motion";
import api from "@/lib/api";
import { Cpu, Wifi, WifiOff, Camera } from "lucide-react";

function relativeAgo(iso) {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const diff = Math.max(0, Date.now() - then);
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

/** Small dashboard card showing which local Windows agents are online,
 *  their last-heartbeat time and how many cameras each is streaming. */
export default function AgentHealthCard() {
  const [agents, setAgents] = useState([]);
  const [err, setErr] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const { data } = await api.get("/agent/status");
        if (!cancelled) { setAgents(Array.isArray(data) ? data : []); setErr(false); }
      } catch {
        if (!cancelled) setErr(true);
      }
    };
    load();
    const t = setInterval(load, 15000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  const onlineCount = agents.filter(a => a.status === "online").length;

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: 0.2 }}
      className="glass rounded-2xl p-5"
      data-testid="agent-health-card"
    >
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Cpu className="h-4 w-4 text-blue-400" />
          <h3 className="font-semibold">Local Camera Agents</h3>
        </div>
        <div className="text-[11px] text-slate-400" data-testid="agent-count">
          {onlineCount}/{agents.length} online
        </div>
      </div>

      {err ? (
        <div className="text-sm text-slate-400">Unable to load agent status.</div>
      ) : agents.length === 0 ? (
        <div className="text-sm text-slate-400" data-testid="agent-empty">
          No local agents connected yet. Install the Windows agent on a showroom PC to start streaming.
        </div>
      ) : (
        <div className="space-y-2" data-testid="agent-list">
          {agents.map((a) => {
            const online = a.status === "online";
            return (
              <div
                key={a.agent_id}
                className="glass-strong rounded-xl px-3 py-2.5 flex items-center justify-between"
                data-testid={`agent-row-${a.agent_id}`}
              >
                <div className="min-w-0">
                  <div className="text-sm font-medium truncate">
                    {a.hostname || a.agent_id}
                    <span className="text-[11px] font-mono text-slate-500 ml-2">{a.agent_id}</span>
                  </div>
                  <div className="text-[11px] text-slate-400 flex items-center gap-3 mt-0.5">
                    <span className="inline-flex items-center gap-1">
                      <Camera className="h-3 w-3" />
                      {(a.cameras || []).length} cam{(a.cameras || []).length === 1 ? "" : "s"}
                    </span>
                    <span>heartbeat {relativeAgo(a.last_heartbeat)}</span>
                  </div>
                </div>
                <div className={`text-[11px] px-2 py-1 rounded-md border inline-flex items-center gap-1 ${
                  online
                    ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/25"
                    : "bg-slate-500/15 text-slate-300 border-slate-500/25"
                }`}>
                  {online ? <Wifi className="h-3 w-3" /> : <WifiOff className="h-3 w-3" />}
                  {online ? "Online" : "Offline"}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </motion.div>
  );
}
