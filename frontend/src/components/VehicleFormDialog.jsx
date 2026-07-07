import React from "react";
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

function Field({ label, value, onChange, test }) {
  return (
    <div>
      <Label className="text-slate-300 text-xs uppercase tracking-widest">{label}</Label>
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-2 bg-white/5 border-white/10 text-white"
        data-testid={test}
      />
    </div>
  );
}

export default function VehicleFormDialog({ open, onOpenChange, mode, form, onFormChange, onSubmit }) {
  const set = (patch) => onFormChange({ ...form, ...patch });
  const title = mode === "create" ? "Add Vehicle" : "Edit Vehicle";
  const submitLabel = mode === "create" ? "Add Vehicle" : "Save Changes";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="bg-[#0F172A]/95 border-white/10 text-white max-w-lg" data-testid="vehicle-form-dialog">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Vehicle Number" value={form.vehicle_number} onChange={(v) => set({ vehicle_number: v })} test="form-vehicle-number" />
          <Field label="Owner Name" value={form.owner_name} onChange={(v) => set({ owner_name: v })} test="form-owner-name" />
          <Field label="Contact" value={form.contact_number} onChange={(v) => set({ contact_number: v })} test="form-contact" />
          <Field label="Model" value={form.vehicle_model} onChange={(v) => set({ vehicle_model: v })} test="form-model" />
          <div className="col-span-2">
            <Label className="text-slate-300 text-xs uppercase tracking-widest">Status</Label>
            <Select value={form.status} onValueChange={(status) => set({ status })}>
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
            <Field label="Notes" value={form.notes} onChange={(v) => set({ notes: v })} test="form-notes" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} className="text-slate-300">Cancel</Button>
          <Button onClick={onSubmit} className="bg-blue-600 hover:bg-blue-700" data-testid="form-submit">
            {submitLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
