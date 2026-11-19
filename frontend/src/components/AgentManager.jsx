import React, { useCallback, useEffect, useState } from "react";
import api, { errorMessage } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import SectionCard from "@/components/common/SectionCard";
import StatusBadge from "@/components/common/StatusBadge";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import SecretRevealDialog from "@/components/common/SecretRevealDialog";
import { ErrorState } from "@/components/common/EmptyState";
import { Cpu, KeyRound, Loader2, Plus, ShieldAlert, Trash2 } from "lucide-react";
import { relativeAgo } from "@/lib/time";

/** Admin: register showroom camera agents and manage their secrets. */
export default function AgentManager() {
  const [agents, setAgents] = useState(null);
  const [error, setError] = useState(false);
  const [newId, setNewId] = useState("");
  const [adding, setAdding] = useState(false);
  const [toRotate, setToRotate] = useState(null);
  const [toDelete, setToDelete] = useState(null);
  const [revealed, setRevealed] = useState(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/agent/status");
      setAgents(Array.isArray(data) ? data : []);
      setError(false);
    } catch {
      setError(true);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const add = async (e) => {
    e.preventDefault();
    const id = newId.trim();
    if (!id) return;
    setAdding(true);
    try {
      const { data } = await api.post("/agent/register", { agent_id: id });
      setRevealed({ label: `VASHU_AGENT_ID=${data.agent_id}`, value: data.secret });
      setNewId("");
      load();
    } catch (err) {
      toast.error("Couldn't register agent", { description: errorMessage(err) });
    } finally {
      setAdding(false);
    }
  };

  const rotate = async () => {
    try {
      const { data } = await api.post(`/agent/${encodeURIComponent(toRotate.agent_id)}/rotate-secret`);
      setRevealed({ label: `VASHU_AGENT_ID=${data.agent_id}`, value: data.secret });
      load();
    } catch (err) {
      toast.error("Couldn't rotate secret", { description: errorMessage(err) });
    }
  };

  const remove = async () => {
    try {
      await api.delete(`/agent/${encodeURIComponent(toDelete.agent_id)}`);
      toast.success("Agent removed", { description: toDelete.agent_id });
      load();
    } catch (err) {
      toast.error("Couldn't remove agent", { description: errorMessage(err) });
    }
  };

  return (
    <SectionCard icon={Cpu} title="Camera agents" description="Showroom PCs allowed to send camera feeds and plate reads" data-testid="agent-manager">
      {error && !agents ? (
        <ErrorState onRetry={load} className="py-6" />
      ) : (
        <div className="space-y-4">
          {(agents || []).map((a) => (
            <div key={a.agent_id} className="flex flex-col gap-2 rounded-lg border px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
                  <span className="font-mono">{a.agent_id}</span>
                  <StatusBadge status={a.status === "online" ? "online" : "offline"} />
                  {!a.has_own_secret && (
                    <span className="inline-flex items-center gap-1 text-xs font-normal text-warning">
                      <ShieldAlert className="h-3.5 w-3.5" /> Uses the shared secret — rotate it
                    </span>
                  )}
                </div>
                <div className="text-xs text-muted-foreground">
                  {a.hostname || "Not connected yet"} · last heartbeat {a.last_heartbeat ? relativeAgo(a.last_heartbeat) : "never"}
                </div>
              </div>
              <div className="flex shrink-0 gap-1">
                <Button size="sm" variant="outline" onClick={() => setToRotate(a)}>
                  <KeyRound className="h-4 w-4" /> Rotate secret
                </Button>
                <Button size="icon-sm" variant="ghost-danger" onClick={() => setToDelete(a)} aria-label={`Remove ${a.agent_id}`}>
                  <Trash2 />
                </Button>
              </div>
            </div>
          ))}
          <form onSubmit={add} className="flex flex-col gap-2 sm:flex-row">
            <Input value={newId} onChange={(e) => setNewId(e.target.value)} placeholder="New agent ID, e.g. showroom-pc-01" className="font-mono text-[13px]" />
            <Button type="submit" disabled={adding || !newId.trim()}>
              {adding ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />} Register agent
            </Button>
          </form>
        </div>
      )}

      <ConfirmDialog
        open={!!toRotate}
        onOpenChange={(v) => !v && setToRotate(null)}
        title={`Rotate secret for ${toRotate?.agent_id}?`}
        description="The agent disconnects until you put the new secret in its .env (VASHU_AGENT_SECRET) and restart it."
        confirmLabel="Rotate secret"
        destructive={false}
        onConfirm={rotate}
      />
      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(v) => !v && setToDelete(null)}
        title={`Remove ${toDelete?.agent_id}?`}
        description="It can no longer connect. Its past records are kept."
        confirmLabel="Remove agent"
        onConfirm={remove}
      />
      <SecretRevealDialog
        secret={revealed}
        title="Agent secret"
        description="Put this in the agent's .env as VASHU_AGENT_SECRET, then restart the agent."
        onClose={() => setRevealed(null)}
      />
    </SectionCard>
  );
}
