import React from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Car, Phone, User, Clock, LogIn as LogInIcon, LogOut, Timer } from "lucide-react";

function formatDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  } catch {
    return iso;
  }
}

function parkingDuration(entry, exit) {
  if (!entry) return "—";
  const end = exit ? new Date(exit) : new Date();
  const ms = end - new Date(entry);
  if (isNaN(ms) || ms < 0) return "—";
  const mins = Math.floor(ms / 60000);
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return `${h}h ${m}m`;
}

const statusColor = {
  inside: "bg-emerald-500/20 text-emerald-300 border-emerald-500/30",
  exited: "bg-slate-500/20 text-slate-300 border-slate-500/30",
  pending: "bg-amber-500/20 text-amber-300 border-amber-500/30",
};

export default function VehicleDetailsDialog({ vehicle, open, onOpenChange }) {
  if (!vehicle) return null;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl bg-[#0F172A]/95 border-white/10 text-white" data-testid="vehicle-details-dialog">
        <DialogHeader>
          <DialogTitle className="text-xl">Vehicle Details</DialogTitle>
        </DialogHeader>

        <div className="grid md:grid-cols-2 gap-6">
          <div className="rounded-xl overflow-hidden border border-white/10 bg-gradient-to-br from-blue-900/40 to-slate-900/50 aspect-video flex items-center justify-center relative">
            <img
              src="https://images.unsplash.com/photo-1580273916550-e323be2ae537?auto=format&fit=crop&w=800&q=60"
              alt="Vehicle"
              className="h-full w-full object-cover opacity-80"
              onError={(e) => { e.currentTarget.style.display = "none"; }}
            />
            <div className="absolute inset-0 bg-gradient-to-t from-[#0F172A] via-transparent" />
            <div className="absolute bottom-3 left-3 right-3 flex items-center justify-between">
              <div className="font-mono text-lg tracking-widest">{vehicle.vehicle_number}</div>
              <Badge className={`${statusColor[vehicle.status] || statusColor.pending} border`}>{vehicle.status}</Badge>
            </div>
          </div>

          <div className="space-y-3.5">
            <Row icon={User} label="Owner Name" value={vehicle.owner_name} />
            <Row icon={Car} label="Vehicle Model" value={vehicle.vehicle_model || "—"} />
            <Row icon={Phone} label="Contact" value={vehicle.contact_number || "—"} />
            <Row icon={LogInIcon} label="Entry Time" value={formatDate(vehicle.entry_time)} />
            <Row icon={LogOut} label="Exit Time" value={formatDate(vehicle.exit_time)} />
            <Row icon={Timer} label="Total Parking Time" value={parkingDuration(vehicle.entry_time, vehicle.exit_time)} />
            <Row icon={Clock} label="Created" value={formatDate(vehicle.created_at)} />
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function Row({ icon: Icon, label, value }) {
  return (
    <div className="flex items-start gap-3">
      <div className="h-9 w-9 rounded-lg bg-white/5 border border-white/10 flex items-center justify-center">
        <Icon className="h-4 w-4 text-blue-400" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-[11px] uppercase tracking-widest text-slate-400">{label}</div>
        <div className="text-sm font-medium truncate">{value}</div>
      </div>
    </div>
  );
}
