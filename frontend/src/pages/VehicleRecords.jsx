import React, { useEffect, useMemo, useState, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import api from "@/lib/api";
import { Button } from "@/components/ui/button";
import VehicleDetailsDialog from "@/components/VehicleDetailsDialog";
import VehicleTable from "@/components/VehicleTable";
import VehicleFilters from "@/components/VehicleFilters";
import VehicleFormDialog from "@/components/VehicleFormDialog";
import { toast } from "sonner";
import { Plus } from "lucide-react";

const EMPTY_FORM = {
  vehicle_number: "",
  owner_name: "",
  contact_number: "",
  vehicle_model: "",
  status: "inside",
  notes: "",
};

function SummaryChips({ rows }) {
  const s = useMemo(() => ({
    total: rows.length,
    inside: rows.filter((r) => r.status === "inside").length,
    exited: rows.filter((r) => r.status === "exited").length,
    pending: rows.filter((r) => r.status === "pending").length,
  }), [rows]);

  const cards = [
    { label: "Total", value: s.total, color: "text-white" },
    { label: "Inside", value: s.inside, color: "text-emerald-300" },
    { label: "Exited", value: s.exited, color: "text-slate-300" },
    { label: "Pending", value: s.pending, color: "text-amber-300" },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      {cards.map((c) => (
        <div key={c.label} className="glass rounded-xl p-4">
          <div className="text-[11px] uppercase tracking-widest text-slate-400">{c.label}</div>
          <div className={`text-2xl font-bold mt-1 ${c.color}`}>{c.value}</div>
        </div>
      ))}
    </div>
  );
}

export default function VehicleRecords() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") || "");
  const [status, setStatus] = useState(params.get("status") || "all");
  const [date, setDate] = useState(null);

  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [hasNext, setHasNext] = useState(false);
  const [hasPrev, setHasPrev] = useState(false);

  const [selected, setSelected] = useState(null);
  const [detailsOpen, setDetailsOpen] = useState(false);

  const [editOpen, setEditOpen] = useState(false);
  const [editMode, setEditMode] = useState("create");
  const [form, setForm] = useState(EMPTY_FORM);
  const [editing, setEditing] = useState(null);

  const PAGE_SIZE = 100;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/vehicles", {
        params: {
          q: q || undefined,
          status: status !== "all" ? status : undefined,
          date: date ? new Date(date).toISOString().slice(0, 10) : undefined,
          page,
          page_size: PAGE_SIZE,
        },
      });
      setRows(data.items || []);
      setTotal(data.total || 0);
      setHasNext(!!data.has_next);
      setHasPrev(!!data.has_prev);
    } catch (e) {
      toast.error("Failed to load", { description: e.message });
    } finally {
      setLoading(false);
    }
  }, [q, status, date, page]);

  useEffect(() => { setPage(1); }, [q, status, date]);
  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const next = new URLSearchParams();
    if (q) next.set("q", q);
    if (status !== "all") next.set("status", status);
    setParams(next, { replace: true });
  }, [q, status, setParams]);

  const openCreate = () => { setEditMode("create"); setForm(EMPTY_FORM); setEditing(null); setEditOpen(true); };
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
  const openView = (v) => { setSelected(v); setDetailsOpen(true); };

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

      <SummaryChips rows={rows} />

      <VehicleFilters q={q} setQ={setQ} status={status} setStatus={setStatus} date={date} setDate={setDate} />

      <VehicleTable rows={rows} loading={loading} onView={openView} onEdit={openEdit} onDelete={remove} />

      {!loading && total > 0 && (
        <div className="flex items-center justify-between px-2 text-xs text-slate-400" data-testid="vehicles-pagination">
          <div>
            Showing <span className="text-slate-200">{(page - 1) * PAGE_SIZE + 1}</span>–
            <span className="text-slate-200">{(page - 1) * PAGE_SIZE + rows.length}</span> of{" "}
            <span className="text-slate-200">{total.toLocaleString()}</span>
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="sm" variant="outline"
              className="h-8 px-3 border-white/10 bg-white/5 hover:bg-white/10 text-slate-200 disabled:opacity-40"
              disabled={!hasPrev}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              data-testid="vehicles-prev"
            >
              Prev
            </Button>
            <span className="tabular-nums">Page {page} / {Math.max(1, Math.ceil(total / PAGE_SIZE))}</span>
            <Button
              size="sm" variant="outline"
              className="h-8 px-3 border-white/10 bg-white/5 hover:bg-white/10 text-slate-200 disabled:opacity-40"
              disabled={!hasNext}
              onClick={() => setPage((p) => p + 1)}
              data-testid="vehicles-next"
            >
              Next
            </Button>
          </div>
        </div>
      )}

      <VehicleDetailsDialog vehicle={selected} open={detailsOpen} onOpenChange={setDetailsOpen} />

      <VehicleFormDialog
        open={editOpen}
        onOpenChange={setEditOpen}
        mode={editMode}
        form={form}
        onFormChange={setForm}
        onSubmit={submitForm}
      />
    </div>
  );
}
