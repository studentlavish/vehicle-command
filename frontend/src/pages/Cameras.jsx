import React, { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import api, { errorMessage } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "sonner";
import PageHeader from "@/components/common/PageHeader";
import SectionCard from "@/components/common/SectionCard";
import StatusBadge from "@/components/common/StatusBadge";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import { EmptyState, ErrorState } from "@/components/common/EmptyState";
import AgentManager from "@/components/AgentManager";
import CameraFormDialog, { CAMERA_ROLES } from "@/components/cameras/CameraFormDialog";
import { relativeAgo } from "@/lib/time";
import { AlertTriangle, Cpu, Link2, Pencil, Plus, PowerOff, Radio, Trash2, Video, VideoOff } from "lucide-react";

const SOURCE_LABEL = { agent: "Local agent", rtsp: "RTSP", http: "IP Webcam", usb: "USB", unknown: "Direct URL", none: "No source" };

/** One place to add cameras, set their role (entry / exit / both), name, gate and active state. */
export default function CamerasPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(false);
  const [form, setForm] = useState({ open: false, camera: null });
  const [toDisconnect, setToDisconnect] = useState(null);
  const [toRemove, setToRemove] = useState(null);
  const [agents, setAgents] = useState([]);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/cameras/overview");
      setRows(Array.isArray(data) ? data : []);
      setError(false);
    } catch {
      setError(true);
    }
  }, []);

  useEffect(() => {
    if (!isAdmin) return;
    api.get("/agent/status").then(({ data }) => setAgents(Array.isArray(data) ? data : [])).catch(() => {});
  }, [isAdmin]);

  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);

  const save = async (row, patch) => {
    const next = { ...row, ...patch };
    setRows((rs) => rs.map((r) => (r.camera_id === row.camera_id ? { ...next, configured: true } : r)));
    try {
      await api.put(`/camera-config/${encodeURIComponent(row.camera_id)}`, {
        role: next.role, name: next.name || "", gate: next.gate || "", enabled: next.enabled !== false,
      });
    } catch (err) {
      toast.error("Couldn't save camera", { description: errorMessage(err) });
      load();
    }
  };

  const setRecords = async (row, useForRecords, agentId) => {
    const patch = { use_for_records: useForRecords, record_agent_id: agentId ?? row.record_agent_id };
    setRows((rs) => rs.map((r) => (r.camera_id === row.camera_id ? { ...r, ...patch } : r)));
    try {
      const { data } = await api.put(`/camera-config/${encodeURIComponent(row.camera_id)}/records`, {
        use_for_records: useForRecords, ...(agentId ? { agent_id: agentId } : {}),
      });
      setRows((rs) => rs.map((r) => (r.camera_id === row.camera_id ? { ...r, record_agent_id: data.record_agent_id } : r)));
      if (useForRecords) toast.success(`${row.camera_id} will be used for records`, { description: `Agent ${data.record_agent_id}` });
    } catch (err) {
      toast.error("Couldn't change \"Use for records\"", { description: errorMessage(err) });
      load();
    }
  };

  const disconnect = async () => {
    try {
      await api.post(`/cameras/${encodeURIComponent(toDisconnect)}/disconnect`);
      toast.success(`${toDisconnect} disconnected`);
      load();
    } catch (e) {
      toast.error("Couldn't disconnect camera", { description: errorMessage(e) });
    }
  };

  const remove = async () => {
    try {
      await api.delete(`/camera-config/${encodeURIComponent(toRemove)}`);
      toast.success(`${toRemove} removed`);
      load();
    } catch (e) {
      toast.error("Couldn't remove camera", { description: errorMessage(e) });
    }
  };

  const list = rows || [];
  const online = list.filter((r) => r.status === "online").length;
  const unassigned = list.filter((r) => !r.configured).length;

  return (
    <div className="space-y-6" data-testid="cameras-page">
      <PageHeader
        icon={Video}
        title="Cameras"
        description="Add cameras and choose which one watches the way in and which the way out."
        actions={
          <>
            <Button variant="outline" asChild>
              <Link to="/live"><Radio className="h-4 w-4" /> Live view</Link>
            </Button>
            <Button onClick={() => setForm({ open: true, camera: null })} data-testid="camera-connect-open">
              <Plus className="h-4 w-4" /> Add camera
            </Button>
          </>
        }
      />

      <SectionCard
        icon={Video}
        title="All cameras"
        description={rows ? `${online} of ${list.length} online${unassigned ? ` · ${unassigned} without a role` : ""}` : "Loading…"}
        noPadding
      >
        {error && !rows ? (
          <ErrorState onRetry={load} className="py-8" />
        ) : !rows ? (
          <div className="space-y-3 p-5">{[0, 1].map((i) => <Skeleton key={i} className="h-20 rounded-lg" />)}</div>
        ) : list.length === 0 ? (
          <EmptyState
            icon={VideoOff}
            title="No cameras yet"
            description="Add an RTSP or IP camera here, or run the local camera agent on a showroom PC — its cameras appear automatically."
            action={<Button variant="outline" onClick={() => setForm({ open: true, camera: null })}><Plus className="h-4 w-4" /> Add camera</Button>}
          />
        ) : (
          <div className="divide-y">
            {list.map((r) => (
              <CameraRow
                key={r.camera_id}
                row={r}
                isAdmin={isAdmin}
                agents={agents}
                onSave={(patch) => save(r, patch)}
                onRecords={(on, agentId) => setRecords(r, on, agentId)}
                onEditSource={() => setForm({ open: true, camera: r })}
                onDisconnect={() => setToDisconnect(r.camera_id)}
                onRemove={() => setToRemove(r.camera_id)}
              />
            ))}
          </div>
        )}
        {rows && list.length > 0 && (
          <p className="border-t px-5 py-3 text-xs text-muted-foreground">
            {CAMERA_ROLES.map((x) => `${x.label}: ${x.hint}.`).join(" ")} An inactive camera still streams but its plate reads are ignored. "Use for records" lets a camera agent read plates from the camera while it is online.
          </p>
        )}
      </SectionCard>

      {isAdmin && <AgentManager />}

      <CameraFormDialog
        open={form.open}
        camera={form.camera}
        canSetRole={isAdmin}
        onOpenChange={(open) => setForm((f) => ({ ...f, open }))}
        onSaved={load}
      />
      <ConfirmDialog
        open={!!toDisconnect}
        onOpenChange={(v) => !v && setToDisconnect(null)}
        title={`Disconnect ${toDisconnect}?`}
        description="The stream stops. Its role and name are kept, so you can connect it again later. Agent cameras come back on their own if the agent is still streaming them."
        confirmLabel="Disconnect"
        onConfirm={disconnect}
      />
      <ConfirmDialog
        open={!!toRemove}
        onOpenChange={(v) => !v && setToRemove(null)}
        title={`Remove ${toRemove}?`}
        description="Its role, name and source are deleted and any direct stream stops. Past visit records are not affected."
        confirmLabel="Remove"
        onConfirm={remove}
      />
    </div>
  );
}

function RecordsToggle({ row: r, isAdmin, agents, onRecords }) {
  const direct = ["rtsp", "http", "usb", "unknown"].includes(r.source_type);
  const on = !!r.use_for_records;
  // Turning it on picks the last-used agent, else the first online one; the dropdown changes it.
  const toggle = (v) => {
    if (!v) return onRecords(false);
    const online = agents.find((a) => a.status === "online");
    onRecords(true, r.record_agent_id || online?.agent_id || agents[0]?.agent_id);
  };
  return (
    <div className="space-y-1.5">
      <label
        className="flex items-center gap-2 text-sm text-muted-foreground"
        title={direct ? "A camera agent reads plates from this camera while it is online" : "Only cameras added with a camera URL can be used for records"}
      >
        <Switch
          checked={on}
          disabled={!isAdmin || (!direct && !on)}
          onCheckedChange={toggle}
          aria-label={`Use ${r.camera_id} for records`}
          data-testid={`camera-records-${r.camera_id}`}
        />
        Use for records
      </label>
      {on && agents.length > 1 && (
        <Select value={r.record_agent_id || ""} onValueChange={(a) => onRecords(true, a)} disabled={!isAdmin}>
          <SelectTrigger className="h-8 text-xs" aria-label={`Agent for ${r.camera_id}`}><SelectValue placeholder="Choose agent" /></SelectTrigger>
          <SelectContent>
            {agents.map((a) => (
              <SelectItem key={a.agent_id} value={a.agent_id}>{a.agent_id}{a.status === "online" ? "" : " (offline)"}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
      {on && agents.length <= 1 && r.record_agent_id && (
        <div className="truncate font-mono text-xs text-muted-foreground">{r.record_agent_id}</div>
      )}
      {on && r.status !== "online" && (
        <div className="text-xs text-warning">Agent will start when this camera is online</div>
      )}
    </div>
  );
}

function CameraRow({ row: r, isAdmin, agents, onSave, onRecords, onEditSource, onDisconnect, onRemove }) {
  const direct = ["rtsp", "http", "usb", "unknown"].includes(r.source_type);
  return (
    <div className="grid gap-3 px-5 py-4 lg:grid-cols-[minmax(0,1.3fr)_160px_minmax(0,1fr)_120px_170px_auto] lg:items-center" data-testid={`camera-row-${r.camera_id}`}>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm font-semibold">{r.camera_id}</span>
          <StatusBadge status={r.status} />
        </div>
        <div className="mt-1 flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
          {r.source_type === "agent" ? <Cpu className="h-3.5 w-3.5 shrink-0" /> : <Link2 className="h-3.5 w-3.5 shrink-0" />}
          <span className="shrink-0">{SOURCE_LABEL[r.source_type] || r.source_type}</span>
          {r.agent_id && <span className="truncate font-mono">· {r.agent_id}</span>}
          {r.source && <span className="truncate font-mono" title={r.source}>· {r.source}</span>}
          {r.last_frame_at && <span className="shrink-0">· {relativeAgo(r.last_frame_at)}</span>}
        </div>
        {!r.configured && (
          <div className="mt-1 inline-flex items-center gap-1 text-xs text-warning">
            <AlertTriangle className="h-3.5 w-3.5" /> No role set — treated as entry + exit
          </div>
        )}
        {r.error && r.status !== "online" && <div className="mt-1 truncate text-xs text-destructive" title={r.error}>{r.error}</div>}
      </div>

      <Select value={r.role} onValueChange={(role) => onSave({ role })} disabled={!isAdmin}>
        <SelectTrigger aria-label={`Role for ${r.camera_id}`} data-testid={`camera-role-${r.camera_id}`}><SelectValue /></SelectTrigger>
        <SelectContent>{CAMERA_ROLES.map((x) => <SelectItem key={x.value} value={x.value}>{x.label}</SelectItem>)}</SelectContent>
      </Select>

      <div className="grid grid-cols-[1fr_90px] gap-2">
        <Input
          key={`n-${r.camera_id}-${r.name}`}
          defaultValue={r.name || ""}
          disabled={!isAdmin}
          placeholder="Name, e.g. Main gate — in"
          aria-label={`Name for ${r.camera_id}`}
          onBlur={(e) => e.target.value !== (r.name || "") && onSave({ name: e.target.value })}
        />
        <Input
          key={`g-${r.camera_id}-${r.gate}`}
          defaultValue={r.gate || ""}
          disabled={!isAdmin}
          placeholder="Gate"
          aria-label={`Gate for ${r.camera_id}`}
          onBlur={(e) => e.target.value !== (r.gate || "") && onSave({ gate: e.target.value })}
        />
      </div>

      <label className="flex items-center gap-2 text-sm text-muted-foreground">
        <Switch checked={r.enabled !== false} disabled={!isAdmin} onCheckedChange={(enabled) => onSave({ enabled })} aria-label={`Enable ${r.camera_id}`} />
        Active
      </label>

      <RecordsToggle row={r} isAdmin={isAdmin} agents={agents} onRecords={onRecords} />

      <div className="flex items-center justify-end gap-1">
        {(direct || r.source_type === "none") && (
          <Button size="icon-sm" variant="ghost" onClick={onEditSource} aria-label="Change source" title="Change source"><Pencil /></Button>
        )}
        {r.live && (
          <Button size="icon-sm" variant="ghost" onClick={onDisconnect} aria-label="Disconnect" title="Disconnect" data-testid={`camera-disconnect-${r.camera_id}`}><PowerOff /></Button>
        )}
        {isAdmin && (
          <Button size="icon-sm" variant="ghost-danger" onClick={onRemove} aria-label="Remove" title="Remove"><Trash2 /></Button>
        )}
      </div>
    </div>
  );
}
