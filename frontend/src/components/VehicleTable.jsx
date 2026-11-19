import React from "react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import StatusBadge from "@/components/common/StatusBadge";
import VehiclePlate from "@/components/common/VehiclePlate";
import { EmptyState } from "@/components/common/EmptyState";
import { TableSkeletonRows } from "@/components/common/Loaders";
import { Eye, Pencil, Trash2, SearchX } from "lucide-react";
import { formatDateTime, elapsed } from "@/lib/time";

function IconAction({ label, children, ...props }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button size="icon-sm" variant="ghost" aria-label={label} {...props}>{children}</Button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

export default function VehicleTable({ rows, loading, onView, onEdit, onDelete, emptyAction, footer, canManage = true }) {
  return (
    <div className="surface overflow-hidden">
      <TooltipProvider delayDuration={300}>
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>Vehicle</th>
                <th>Owner</th>
                <th>Contact</th>
                <th>Entry</th>
                <th>Exit</th>
                <th>Duration</th>
                <th>Status</th>
                <th className="text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <TableSkeletonRows rows={8} cols={8} />
              ) : rows.length === 0 ? (
                <tr className="hover:bg-transparent">
                  <td colSpan={8}>
                    <EmptyState icon={SearchX} title="No vehicles found" description="Try a different search or clear the filters." action={emptyAction} />
                  </td>
                </tr>
              ) : (
                rows.map((v) => (
                  <tr key={v.id} data-testid={`row-${v.vehicle_number}`}>
                    <td><VehiclePlate number={v.vehicle_number} data-testid={`goto-detail-${v.vehicle_number}`} /></td>
                    <td>
                      <div className="font-medium">{v.owner_name}</div>
                      <div className="text-xs text-muted-foreground">{v.vehicle_model || "—"}</div>
                    </td>
                    <td className="whitespace-nowrap text-muted-foreground">{v.contact_number || "—"}</td>
                    <td className="whitespace-nowrap text-muted-foreground">{formatDateTime(v.entry_time)}</td>
                    <td className="whitespace-nowrap text-muted-foreground">{formatDateTime(v.exit_time)}</td>
                    <td className="whitespace-nowrap tabular-nums text-muted-foreground">{elapsed(v.entry_time, v.exit_time)}</td>
                    <td><StatusBadge status={v.status} /></td>
                    <td className="text-right">
                      <div className="inline-flex gap-0.5">
                        <IconAction label="View details" onClick={() => onView(v)} data-testid={`view-${v.vehicle_number}`}><Eye /></IconAction>
                        {canManage && <IconAction label="Edit" onClick={() => onEdit(v)} data-testid={`edit-${v.vehicle_number}`}><Pencil /></IconAction>}
                        {canManage && <IconAction label="Delete" variant="ghost-danger" onClick={() => onDelete(v)} data-testid={`delete-${v.vehicle_number}`}><Trash2 /></IconAction>}
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </TooltipProvider>
      {footer}
    </div>
  );
}
