import React from "react";
import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Eye, Pencil, Trash2 } from "lucide-react";

const STATUS_STYLE = {
  inside: "bg-emerald-500/15 text-emerald-300 border-emerald-500/25",
  exited: "bg-slate-500/15 text-slate-300 border-slate-500/25",
  pending: "bg-amber-500/15 text-amber-300 border-amber-500/25",
};

function formatTime(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" }); } catch { return iso; }
}

function Row({ v, onView, onEdit, onDelete }) {
  return (
    <tr className="hover:bg-white/[0.03]" data-testid={`row-${v.vehicle_number}`}>
      <td className="px-5 py-3 font-mono">
        <Link
          to={`/vehicle/${encodeURIComponent(v.vehicle_number)}`}
          className="text-white hover:text-blue-300 transition-colors"
          data-testid={`goto-detail-${v.vehicle_number}`}
        >
          {v.vehicle_number}
        </Link>
      </td>
      <td className="px-5 py-3">
        <div className="text-white">{v.owner_name}</div>
        <div className="text-[11px] text-slate-500">{v.vehicle_model}</div>
      </td>
      <td className="px-5 py-3 text-slate-300">{v.contact_number || "—"}</td>
      <td className="px-5 py-3 text-slate-300">{formatTime(v.entry_time)}</td>
      <td className="px-5 py-3 text-slate-300">{formatTime(v.exit_time)}</td>
      <td className="px-5 py-3">
        <Badge className={`${STATUS_STYLE[v.status]} border capitalize`}>{v.status}</Badge>
      </td>
      <td className="px-5 py-3 text-right">
        <div className="inline-flex gap-1">
          <Button size="icon" variant="ghost" onClick={() => onView(v)} className="h-8 w-8 text-blue-400 hover:text-white hover:bg-blue-600/20" data-testid={`view-${v.vehicle_number}`}>
            <Eye className="h-4 w-4" />
          </Button>
          <Button size="icon" variant="ghost" onClick={() => onEdit(v)} className="h-8 w-8 text-slate-300 hover:bg-white/5" data-testid={`edit-${v.vehicle_number}`}>
            <Pencil className="h-4 w-4" />
          </Button>
          <Button size="icon" variant="ghost" onClick={() => onDelete(v)} className="h-8 w-8 text-rose-400 hover:bg-rose-500/10" data-testid={`delete-${v.vehicle_number}`}>
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </td>
    </tr>
  );
}

export default function VehicleTable({ rows, loading, onView, onEdit, onDelete }) {
  return (
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
                <tr key={`v-skel-${i}`}><td colSpan={7} className="p-3"><Skeleton className="h-8 bg-white/5" /></td></tr>
              ))
            ) : rows.length === 0 ? (
              <tr><td colSpan={7} className="text-center py-14 text-slate-400">No matching vehicles found.</td></tr>
            ) : (
              rows.map((v) => <Row key={v.id} v={v} onView={onView} onEdit={onEdit} onDelete={onDelete} />)
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
