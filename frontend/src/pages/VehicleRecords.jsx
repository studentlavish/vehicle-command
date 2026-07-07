import React, { useEffect, useMemo, useState, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import api from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Calendar } from "@/components/ui/calendar";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import VehicleDetailsDialog from "@/components/VehicleDetailsDialog";
import { toast } from "sonner";
import {
  Search,
  Filter,
  CalendarIcon,
  Eye,
  Pencil,
  Trash2,
  Plus,
  X,
} from "lucide-react";

const statusStyle = {
  inside: "bg-emerald-500/15 text-emerald-300 border-emerald-500/25",
  exited: "bg-slate-500/15 text-slate-300 border-slate-500/25",
  pending: "bg-amber-500/15 text-amber-300 border-amber-500/25",
};

function formatTime(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" }); } catch { return iso; }
}

const emptyForm = { vehicle_number: "", owner_name: "", contact_number: "", vehicle_model: "", status: "inside", notes: "" };

export default function VehicleRecords() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") || "");
  const [status, setStatus] = useState(params.get("status") || "all");
  const [date, setDate] = useState(null);
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null);
  const [detailsOpen, setDetailsOpen] = useState(false);

  const [editOpen, setEditOpen] = useState(false);
  const [editMode, setEditMode] = useState("create");
  const [form, setForm] = useState(emptyForm);
  const [editing, setEditing] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/vehicles", {
        params: {
          q: q || undefined,
          status: status !== "all" ? status : undefined,
          date: date ? new Date(date).toISOString().slice(0, 10) : undefined,
          limit: 500,
        },
      });
      setRows(data);
    } catch (e) {
      toast.error("Failed to load", { description: e.message });
    } finally {
      setLoading(false);
    }
  }, [q, status, date]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const next = new URLSearchParams();
    if (q) next.set("q", q);
    if (status !== "all") next.set("status", status);
    setParams(next, { replace: true });
  }, [q, status, setParams]);

  const openCreate = () => { setEditMode("create"); setForm(emptyForm); setEditing(null); setEditOpen(true); };
  const openEdit = (v) => {
    setEditMode("edit");
    setEditing(v);
    setForm({
      vehicle_number: v.vehicle_number,
      owner_name: v.owner_name,
      contact_number: v.contact_number || "",
      vehicle_model: v.vehicle_model || "",
      status: v.status,
      notes: v.notes || "",
    });
    setEditOpen(true);
  };

  const submitForm = async () => {
    try {
      if (editMode === "create") {
        await api.post("/vehicles", form);
        toast.success("Vehicle added");
      } else {
        await api.patch(`/vehicles/${editing.id}`, form);
        toast.success("Vehicle updated");
      }
      setEditOpen(false);
      load();
    } catch (e) {
      toast.error("Save failed", { description: e.response?.data?.detail || e.message });
    }
  };

  const remove = async (v) => {
    if (!window.confirm(`Delete ${v.vehicle_number}?`)) return;
    try {
      await api.delete(`/vehicles/${v.id}`);
      toast.success("Vehicle deleted");
      load();
    } catch (e) {
      toast.error("Delete failed", { description: e.message });
    }
  };

  const summary = useMemo(() => ({
    total: rows.length,
    inside: rows.filter((r) => r.status === "inside").length,
    exited: rows.filter((r) => r.status === "exited").length,
    pending: rows.filter((r) => r.status === "pending").length,
  }), [rows]);

  return (
    <div className="space-y-6" data-testid="vehicles-page">
      <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-4">
        <div>
          <h1 className="text-4xl md:text-5xl font-bold tracking-tight">Vehicle Records</h1>
          <p className="text-slate-400 mt-2 text-sm">Full ledger of every vehicle that has passed through the showroom.</p>
        </div>
        <Button onClick={openCreate} className="bg-blue-600 hover:bg-blue-700 text-white" data-testid="add-vehicle-btn">
          <Plus className="h-4 w-4 mr-2" /> Add Vehicle
        </Button>
      </div>

      {/* Summary chips */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          { label: "Total", value: summary.total, color: "text-white" },
          { label: "Inside", value: summary.inside, color: "text-emerald-300" },
          { label: "Exited", value: summary.exited, color: "text-slate-300" },
          { label: "Pending", value: summary.pending, color: "text-amber-300" },
        ].map((s) => (
          <div key={s.label} className="glass rounded-xl p-4">
            <div className="text-[11px] uppercase tracking-widest text-slate-400">{s.label}</div>
            <div className={`text-2xl font-bold mt-1 ${s.color}`}>{s.value}</div>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div className="glass rounded-2xl p-4 grid gap-3 md:grid-cols-12 items-center">
        <div className="md:col-span-5 relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" />
          <Input
            data-testid="vehicles-search-input"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search vehicle number or owner..."
            className="pl-9 h-10 bg-white/5 border-white/10 text-white placeholder:text-slate-500"
          />
        </div>

        <div className="md:col-span-3">
          <Popover>
            <PopoverTrigger asChild>
              <Button variant="outline" className="w-full h-10 justify-start bg-white/5 border-white/10 text-white hover:bg-white/10" data-testid="date-filter-btn">
                <CalendarIcon className="h-4 w-4 mr-2 text-slate-400" />
                {date ? new Date(date).toLocaleDateString() : "Filter by date"}
              </Button>
            </PopoverTrigger>
            <PopoverContent className="p-0 bg-[#0F172A]/95 border-white/10" align="start">
              <Calendar mode="single" selected={date} onSelect={setDate} initialFocus />
            </PopoverContent>
          </Popover>
        </div>

        <div className="md:col-span-3">
          <Select value={status} onValueChange={setStatus}>
            <SelectTrigger className="h-10 bg-white/5 border-white/10 text-white" data-testid="status-filter">
              <Filter className="h-4 w-4 mr-2 text-slate-400" />
              <SelectValue placeholder="Status" />
            </SelectTrigger>
            <SelectContent className="bg-[#0F172A]/95 border-white/10 text-white">
              <SelectItem value="all">All statuses</SelectItem>
              <SelectItem value="inside">Inside</SelectItem>
              <SelectItem value="exited">Exited</SelectItem>
              <SelectItem value="pending">Pending</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="md:col-span-1 flex justify-end">
          {(q || status !== "all" || date) && (
            <Button variant="ghost" onClick={() => { setQ(""); setStatus("all"); setDate(null); }} className="text-slate-400 hover:text-white" data-testid="clear-filters">
              <X className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>

      {/* Table */}
      <div className="glass rounded-2xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-slate-400 text-[11px] uppercase tracking-widest border-b border-white/5">
                <th className="px-5 py-3 font-normal">Vehicle #</th>
                <th className="px-5 py-3 font-normal">Owner</th>
                <th className="px-5 py-3 font-normal">Contact</th>
                <th className="px-5 py-3 font-normal">Entry</th>
                <th className="px-5 py-3 font-normal">Exit</th>
                <th className="px-5 py-3 font-normal">Status</th>
                <th className="px-5 py-3 font-normal text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {loading ? (
                Array.from({ length: 8 }).map((_, i) => (
                  <tr key={i}><td colSpan={7} className="p-3"><Skeleton className="h-8 bg-white/5" /></td></tr>
                ))
              ) : rows.length === 0 ? (
                <tr><td colSpan={7} className="text-center py-14 text-slate-400">No matching vehicles found.</td></tr>
              ) : (
                rows.map((v) => (
                  <tr key={v.id} className="hover:bg-white/[0.03]" data-testid={`row-${v.vehicle_number}`}>
                    <td className="px-5 py-3 font-mono text-white">{v.vehicle_number}</td>
                    <td className="px-5 py-3">
                      <div className="text-white">{v.owner_name}</div>
                      <div className="text-[11px] text-slate-500">{v.vehicle_model}</div>
                    </td>
                    <td className="px-5 py-3 text-slate-300">{v.contact_number || "—"}</td>
                    <td className="px-5 py-3 text-slate-300">{formatTime(v.entry_time)}</td>
                    <td className="px-5 py-3 text-slate-300">{formatTime(v.exit_time)}</td>
                    <td className="px-5 py-3">
                      <Badge className={`${statusStyle[v.status]} border capitalize`}>{v.status}</Badge>
                    </td>
                    <td className="px-5 py-3 text-right">
                      <div className="inline-flex gap-1">
                        <Button size="icon" variant="ghost" onClick={() => { setSelected(v); setDetailsOpen(true); }} className="h-8 w-8 text-blue-400 hover:text-white hover:bg-blue-600/20" data-testid={`view-${v.vehicle_number}`}>
                          <Eye className="h-4 w-4" />
                        </Button>
                        <Button size="icon" variant="ghost" onClick={() => openEdit(v)} className="h-8 w-8 text-slate-300 hover:bg-white/5" data-testid={`edit-${v.vehicle_number}`}>
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button size="icon" variant="ghost" onClick={() => remove(v)} className="h-8 w-8 text-rose-400 hover:bg-rose-500/10" data-testid={`delete-${v.vehicle_number}`}>
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      <VehicleDetailsDialog vehicle={selected} open={detailsOpen} onOpenChange={setDetailsOpen} />

      {/* Create/Edit Dialog */}
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="bg-[#0F172A]/95 border-white/10 text-white max-w-lg" data-testid="vehicle-form-dialog">
          <DialogHeader>
            <DialogTitle>{editMode === "create" ? "Add Vehicle" : "Edit Vehicle"}</DialogTitle>
          </DialogHeader>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Vehicle Number" v={form.vehicle_number} onChange={(vehicle_number) => setForm({ ...form, vehicle_number })} test="form-vehicle-number" />
            <Field label="Owner Name" v={form.owner_name} onChange={(owner_name) => setForm({ ...form, owner_name })} test="form-owner-name" />
            <Field label="Contact" v={form.contact_number} onChange={(contact_number) => setForm({ ...form, contact_number })} test="form-contact" />
            <Field label="Model" v={form.vehicle_model} onChange={(vehicle_model) => setForm({ ...form, vehicle_model })} test="form-model" />
            <div className="col-span-2">
              <Label className="text-slate-300 text-xs uppercase tracking-widest">Status</Label>
              <Select value={form.status} onValueChange={(status) => setForm({ ...form, status })}>
                <SelectTrigger className="mt-2 bg-white/5 border-white/10 text-white" data-testid="form-status">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="bg-[#0F172A]/95 border-white/10 text-white">
                  <SelectItem value="inside">Inside</SelectItem>
                  <SelectItem value="exited">Exited</SelectItem>
                  <SelectItem value="pending">Pending</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="col-span-2">
              <Field label="Notes" v={form.notes} onChange={(notes) => setForm({ ...form, notes })} test="form-notes" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setEditOpen(false)} className="text-slate-300">Cancel</Button>
            <Button onClick={submitForm} className="bg-blue-600 hover:bg-blue-700" data-testid="form-submit">
              {editMode === "create" ? "Add Vehicle" : "Save Changes"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Field({ label, v, onChange, test }) {
  return (
    <div>
      <Label className="text-slate-300 text-xs uppercase tracking-widest">{label}</Label>
      <Input
        value={v}
        onChange={(e) => onChange(e.target.value)}
        className="mt-2 bg-white/5 border-white/10 text-white"
        data-testid={test}
      />
    </div>
  );
}
