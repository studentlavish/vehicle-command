import React, { useCallback, useEffect, useState } from "react";
import api, { errorMessage } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "sonner";
import SectionCard from "@/components/common/SectionCard";
import { EmptyState, ErrorState } from "@/components/common/EmptyState";
import { Camera, AlertTriangle } from "lucide-react";

const ROLES = [
  { value: "entry", label: "Entry", hint: "Only records cars coming in" },
  { value: "exit", label: "Exit", hint: "Only records cars going out" },
  { value: "both", label: "Entry + exit", hint: "One camera for both directions" },
];

/** Admin: tell the system which camera watches the way in and which the way out. */
export default function CameraRoles() {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/camera-config");
      setRows(Array.isArray(data) ? data : []);
      setError(false);
    } catch {
      setError(true);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

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

  return (
    <SectionCard icon={Camera} title="Cameras" description="Which camera watches the way in and which the way out" data-testid="camera-roles">
      {error && !rows ? (
        <ErrorState onRetry={load} className="py-6" />
      ) : rows && rows.length === 0 ? (
        <EmptyState compact icon={Camera} title="No cameras yet" description="Cameras appear here once an agent streams them." />
      ) : (
        <div className="space-y-3">
          {(rows || []).map((r) => (
            <div key={r.camera_id} className="grid gap-3 rounded-lg border p-3 md:grid-cols-[1fr_180px_1fr_auto] md:items-center" data-testid={`camera-role-${r.camera_id}`}>
              <div className="min-w-0">
                <div className="font-mono text-sm font-medium">{r.camera_id}</div>
                {!r.configured && (
                  <div className="mt-0.5 inline-flex items-center gap-1 text-xs text-warning">
                    <AlertTriangle className="h-3.5 w-3.5" /> No role set — treated as entry + exit
                  </div>
                )}
              </div>
              <Select value={r.role} onValueChange={(role) => save(r, { role })}>
                <SelectTrigger aria-label={`Role for ${r.camera_id}`}><SelectValue /></SelectTrigger>
                <SelectContent>
                  {ROLES.map((x) => <SelectItem key={x.value} value={x.value}>{x.label}</SelectItem>)}
                </SelectContent>
              </Select>
              <Input
                defaultValue={r.name || ""}
                placeholder="Label, e.g. Main gate — in"
                onBlur={(e) => e.target.value !== (r.name || "") && save(r, { name: e.target.value })}
              />
              <label className="flex items-center gap-2 text-sm text-muted-foreground">
                <Switch checked={r.enabled !== false} onCheckedChange={(enabled) => save(r, { enabled })} aria-label={`Enable ${r.camera_id}`} />
                Active
              </label>
            </div>
          ))}
          <p className="text-xs text-muted-foreground">
            {ROLES.map((x) => `${x.label}: ${x.hint}.`).join(" ")} An inactive camera still streams but its plate reads are ignored.
          </p>
        </div>
      )}
    </SectionCard>
  );
}
