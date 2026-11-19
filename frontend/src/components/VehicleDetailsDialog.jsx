import React from "react";
import { Link } from "react-router-dom";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import StatusBadge from "@/components/common/StatusBadge";
import { assetUrl } from "@/lib/api";
import { Car, Phone, User, LogIn as LogInIcon, LogOut, Timer, ArrowRight, Camera, StickyNote } from "lucide-react";
import { formatDateTime, elapsed } from "@/lib/time";

function Row({ icon: Icon, label, value }) {
  return (
    <div className="flex items-start gap-3 py-2.5">
      <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
      <div className="flex min-w-0 flex-1 items-start justify-between gap-4">
        <span className="text-sm text-muted-foreground">{label}</span>
        <span className="truncate text-right text-sm font-medium">{value || "—"}</span>
      </div>
    </div>
  );
}

export default function VehicleDetailsDialog({ vehicle, open, onOpenChange }) {
  if (!vehicle) return null;
  const img = assetUrl(vehicle.entry_image);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl" data-testid="vehicle-details-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-3">
            <span className="plate text-base">{vehicle.vehicle_number}</span>
            <StatusBadge status={vehicle.status} />
          </DialogTitle>
          <DialogDescription>Visit details for this vehicle.</DialogDescription>
        </DialogHeader>

        <div className="grid gap-6 md:grid-cols-2">
          <div className="relative flex aspect-video items-center justify-center overflow-hidden rounded-lg border bg-muted">
            {img ? (
              <img src={img} alt={`${vehicle.vehicle_number} at entry`} className="h-full w-full object-cover" onError={(e) => { e.currentTarget.style.display = "none"; }} />
            ) : (
              <div className="flex flex-col items-center gap-2 text-muted-foreground">
                <Camera className="h-8 w-8" />
                <span className="text-xs">No entry snapshot</span>
              </div>
            )}
          </div>

          <div className="divide-y">
            <Row icon={User} label="Owner" value={vehicle.owner_name} />
            <Row icon={Car} label="Model" value={vehicle.vehicle_model} />
            <Row icon={Phone} label="Contact" value={vehicle.contact_number} />
            <Row icon={LogInIcon} label="Entry" value={formatDateTime(vehicle.entry_time)} />
            <Row icon={LogOut} label="Exit" value={vehicle.exit_time ? formatDateTime(vehicle.exit_time) : "Still inside"} />
            <Row icon={Timer} label="Time inside" value={elapsed(vehicle.entry_time, vehicle.exit_time)} />
            {vehicle.notes ? <Row icon={StickyNote} label="Notes" value={vehicle.notes} /> : null}
          </div>
        </div>

        <div className="flex justify-end border-t pt-4">
          <Button asChild variant="outline" onClick={() => onOpenChange(false)}>
            <Link to={`/vehicle/${encodeURIComponent(vehicle.vehicle_number)}`}>
              Open full profile <ArrowRight className="h-4 w-4" />
            </Link>
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
