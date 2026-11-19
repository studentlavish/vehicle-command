import React, { useEffect, useState, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import api, { errorMessage } from "@/lib/api";
import { Button } from "@/components/ui/button";
import VehicleDetailsDialog from "@/components/VehicleDetailsDialog";
import VehicleTable from "@/components/VehicleTable";
import VehicleFilters from "@/components/VehicleFilters";
import VehicleFormDialog from "@/components/VehicleFormDialog";
import PageHeader from "@/components/common/PageHeader";
import Pagination from "@/components/common/Pagination";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import { ErrorState } from "@/components/common/EmptyState";
import { toast } from "sonner";
import { Plus, Car } from "lucide-react";

const EMPTY_FORM = { vehicle_number: "", owner_name: "", contact_number: "", vehicle_model: "", status: "inside", notes: "" };
const PAGE_SIZE = 50;

function useDebounced(value, ms = 300) {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}

export default function VehicleRecords() {
  const { user } = useAuth();
  const canManage = user?.role === "admin" || user?.role === "manager";
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") || "");
  const [status, setStatus] = useState(params.get("status") || "all");
  const [date, setDate] = useState(null);
  const dq = useDebounced(q);

  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [page, setPage] = useState(1);
  const [meta, setMeta] = useState({ total: 0, hasNext: false, hasPrev: false });

  const [selected, setSelected] = useState(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editMode, setEditMode] = useState("create");
  const [form, setForm] = useState(EMPTY_FORM);
  const [editing, setEditing] = useState(null);
  const [toDelete, setToDelete] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const { data } = await api.get("/vehicles", {
        params: {
          q: dq || undefined,
          status: status !== "all" ? status : undefined,
          date: date ? new Date(date).toLocaleDateString("en-CA") : undefined,
          page,
          page_size: PAGE_SIZE,
        },
      });
      setRows(data.items || []);
      setMeta({ total: data.total || 0, hasNext: !!data.has_next, hasPrev: !!data.has_prev });
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [dq, status, date, page]);

  useEffect(() => { setPage(1); }, [dq, status, date]);
  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const next = new URLSearchParams();
    if (dq) next.set("q", dq);
    if (status !== "all") next.set("status", status);
    setParams(next, { replace: true });
  }, [dq, status, setParams]);

  const openCreate = () => { setEditMode("create"); setForm(EMPTY_FORM); setEditing(null); setEditOpen(true); };
  const openEdit = (v) => {
    setEditMode("edit");
    setEditing(v);
    setForm({
      vehicle_number: v.vehicle_number,
      owner_name: v.owner_name || "",
      contact_number: v.contact_number || "",
      vehicle_model: v.vehicle_model || "",
      status: v.status || "inside",
      notes: v.notes || "",
    });
    setEditOpen(true);
  };

  const submitForm = async () => {
    try {
      if (editMode === "create") {
        await api.post("/vehicles", form);
        toast.success("Vehicle added", { description: form.vehicle_number });
      } else {
        await api.patch(`/vehicles/${editing.id}`, form);
        toast.success("Changes saved", { description: form.vehicle_number });
      }
      setEditOpen(false);
      load();
    } catch (e) {
      toast.error("Couldn't save vehicle", { description: errorMessage(e) });
    }
  };

  const remove = async () => {
    try {
      await api.delete(`/vehicles/${toDelete.id}`);
      toast.success("Vehicle deleted", { description: toDelete.vehicle_number });
      load();
    } catch (e) {
      toast.error("Couldn't delete vehicle", { description: errorMessage(e) });
    }
  };

  return (
    <div className="space-y-6" data-testid="vehicles-page">
      <PageHeader
        icon={Car}
        title="Vehicle records"
        description={loading ? "Loading records…" : `${meta.total.toLocaleString()} visit record${meta.total === 1 ? "" : "s"} in the last 180 days`}
        actions={
          <Button onClick={openCreate} data-testid="add-vehicle-btn">
            <Plus className="h-4 w-4" /> Add vehicle
          </Button>
        }
      />

      <VehicleFilters q={q} setQ={setQ} status={status} setStatus={setStatus} date={date} setDate={setDate} loading={loading && !!q} />

      {error ? (
        <div className="surface"><ErrorState onRetry={load} /></div>
      ) : (
        <VehicleTable
          rows={rows}
          loading={loading}
          onView={(v) => { setSelected(v); setDetailsOpen(true); }}
          onEdit={openEdit}
          onDelete={setToDelete}
          canManage={canManage}
          emptyAction={(q || status !== "all" || date) ? (
            <Button variant="outline" size="sm" onClick={() => { setQ(""); setStatus("all"); setDate(null); }}>Clear filters</Button>
          ) : (
            <Button size="sm" onClick={openCreate}><Plus className="h-4 w-4" /> Add vehicle</Button>
          )}
          footer={!loading && (
            <Pagination
              page={page} pageSize={PAGE_SIZE} total={meta.total} count={rows.length}
              hasPrev={meta.hasPrev} hasNext={meta.hasNext}
              onPrev={() => setPage((p) => Math.max(1, p - 1))}
              onNext={() => setPage((p) => p + 1)}
              testIdPrefix="vehicles"
            />
          )}
        />
      )}

      <VehicleDetailsDialog vehicle={selected} open={detailsOpen} onOpenChange={setDetailsOpen} />
      <VehicleFormDialog open={editOpen} onOpenChange={setEditOpen} mode={editMode} form={form} onFormChange={setForm} onSubmit={submitForm} />
      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(v) => !v && setToDelete(null)}
        title={`Delete ${toDelete?.vehicle_number || "vehicle"}?`}
        description="This visit record will be permanently removed. This can't be undone."
        confirmLabel="Delete vehicle"
        onConfirm={remove}
      />
    </div>
  );
}
