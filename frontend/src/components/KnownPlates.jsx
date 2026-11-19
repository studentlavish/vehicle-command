import React, { useCallback, useEffect, useState } from "react";
import api, { errorMessage } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "sonner";
import SectionCard from "@/components/common/SectionCard";
import VehiclePlate from "@/components/common/VehiclePlate";
import { ErrorState } from "@/components/common/EmptyState";
import { BadgeCheck, Loader2, Plus, Trash2 } from "lucide-react";

const KINDS = [
  { value: "demo", label: "Demo car" },
  { value: "test_drive", label: "Test-drive car" },
  { value: "trade", label: "Trade / temporary plate" },
  { value: "staff", label: "Staff vehicle" },
  { value: "other", label: "Other" },
];
const KIND_LABEL = Object.fromEntries(KINDS.map((k) => [k.value, k.label]));
const EMPTY = { plate: "", label: "", kind: "demo" };

/** Vehicles the showroom owns or expects — their reads are trusted more. */
export default function KnownPlates() {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/known-plates");
      setRows(Array.isArray(data) ? data : []);
      setError(false);
    } catch {
      setError(true);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const add = async (e) => {
    e.preventDefault();
    if (!form.plate.trim()) return;
    setSaving(true);
    try {
      await api.post("/known-plates", form);
      setForm(EMPTY);
      load();
    } catch (err) {
      toast.error("Couldn't add plate", { description: errorMessage(err) });
    } finally {
      setSaving(false);
    }
  };

  const remove = async (plate) => {
    try {
      await api.delete(`/known-plates/${encodeURIComponent(plate)}`);
      setRows((rs) => rs.filter((r) => r.plate !== plate));
    } catch (err) {
      toast.error("Couldn't remove plate", { description: errorMessage(err) });
    }
  };

  return (
    <SectionCard icon={BadgeCheck} title="Known plates" description="Demo, test-drive, trade and staff vehicles — reads of these are trusted more" data-testid="known-plates">
      {error && !rows ? (
        <ErrorState onRetry={load} className="py-6" />
      ) : (
        <div className="space-y-4">
          {rows && rows.length > 0 && (
            <ul className="divide-y rounded-lg border">
              {rows.map((r) => (
                <li key={r.plate} className="flex items-center justify-between gap-3 px-3 py-2">
                  <div className="flex min-w-0 items-center gap-3">
                    <VehiclePlate number={r.plate} />
                    <div className="min-w-0 text-sm">
                      <div className="truncate">{r.label || KIND_LABEL[r.kind]}</div>
                      <div className="text-xs text-muted-foreground">{KIND_LABEL[r.kind]}{r.standard ? "" : " · non-standard format"}</div>
                    </div>
                  </div>
                  <Button size="icon-sm" variant="ghost-danger" onClick={() => remove(r.plate)} aria-label={`Remove ${r.plate}`}>
                    <Trash2 />
                  </Button>
                </li>
              ))}
            </ul>
          )}
          <form onSubmit={add} className="grid gap-2 md:grid-cols-[1fr_1fr_200px_auto]">
            <Input value={form.plate} onChange={(e) => setForm({ ...form, plate: e.target.value.toUpperCase() })} placeholder="Plate, e.g. KA05MG9999" className="font-mono" />
            <Input value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} placeholder="Label, e.g. Creta demo (white)" />
            <Select value={form.kind} onValueChange={(kind) => setForm({ ...form, kind })}>
              <SelectTrigger aria-label="Kind"><SelectValue /></SelectTrigger>
              <SelectContent>{KINDS.map((k) => <SelectItem key={k.value} value={k.value}>{k.label}</SelectItem>)}</SelectContent>
            </Select>
            <Button type="submit" disabled={saving || !form.plate.trim()}>
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />} Add
            </Button>
          </form>
        </div>
      )}
    </SectionCard>
  );
}
