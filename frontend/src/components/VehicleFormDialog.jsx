import React, { useEffect, useState } from "react";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Loader2, Car } from "lucide-react";
import { cn } from "@/lib/utils";

function Field({ label, required, error, children, className }) {
  return (
    <div className={className}>
      <label className="field-label">
        {label} {required && <span className="text-destructive">*</span>}
      </label>
      {children}
      {error ? <p className="mt-1 text-xs text-destructive">{error}</p> : null}
    </div>
  );
}

export default function VehicleFormDialog({ open, onOpenChange, mode, form, onFormChange, onSubmit }) {
  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState({});
  const set = (patch) => onFormChange({ ...form, ...patch });
  const isCreate = mode === "create";

  useEffect(() => { if (open) setErrors({}); }, [open]);

  const submit = async (e) => {
    e.preventDefault();
    const errs = {};
    if (!form.vehicle_number.trim()) errs.vehicle_number = "Vehicle number is required";
    if (!form.owner_name.trim()) errs.owner_name = "Owner name is required";
    if (form.contact_number && !/^[+\d][\d\s-]{6,}$/.test(form.contact_number.trim())) errs.contact_number = "Enter a valid phone number";
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setSaving(true);
    try { await onSubmit(); } finally { setSaving(false); }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !saving && onOpenChange(v)}>
      <DialogContent className="max-w-lg" data-testid="vehicle-form-dialog">
        <form onSubmit={submit} className="space-y-5">
          <DialogHeader>
            <div className="flex items-center gap-3">
              <div className="icon-tile"><Car className="h-5 w-5" /></div>
              <div className="text-left">
                <DialogTitle>{isCreate ? "Add vehicle" : "Edit vehicle"}</DialogTitle>
                <DialogDescription>{isCreate ? "Record a new vehicle visit." : "Update the details for this visit."}</DialogDescription>
              </div>
            </div>
          </DialogHeader>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Vehicle number" required error={errors.vehicle_number}>
              <Input
                value={form.vehicle_number}
                onChange={(e) => set({ vehicle_number: e.target.value.toUpperCase() })}
                placeholder="e.g. UP21AB1234"
                className={cn("font-mono uppercase", errors.vehicle_number && "border-destructive")}
                data-testid="form-vehicle-number"
                autoFocus
              />
            </Field>
            <Field label="Owner name" required error={errors.owner_name}>
              <Input value={form.owner_name} onChange={(e) => set({ owner_name: e.target.value })} placeholder="Full name" className={cn(errors.owner_name && "border-destructive")} data-testid="form-owner-name" />
            </Field>
            <Field label="Contact number" error={errors.contact_number}>
              <Input value={form.contact_number} onChange={(e) => set({ contact_number: e.target.value })} placeholder="+91 98765 43210" className={cn(errors.contact_number && "border-destructive")} data-testid="form-contact" />
            </Field>
            <Field label="Model">
              <Input value={form.vehicle_model} onChange={(e) => set({ vehicle_model: e.target.value })} placeholder="e.g. Hyundai Creta" data-testid="form-model" />
            </Field>
            <Field label="Status" className="sm:col-span-2">
              <Select value={form.status} onValueChange={(status) => set({ status })}>
                <SelectTrigger data-testid="form-status"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="inside">Inside</SelectItem>
                  <SelectItem value="exited">Exited</SelectItem>
                  <SelectItem value="pending">Pending</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field label="Notes" className="sm:col-span-2">
              <Textarea value={form.notes} onChange={(e) => set({ notes: e.target.value })} rows={3} placeholder="Optional notes" className="resize-none bg-card" data-testid="form-notes" />
            </Field>
          </div>

          <DialogFooter className="gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>Cancel</Button>
            <Button type="submit" disabled={saving} data-testid="form-submit">
              {saving && <Loader2 className="h-4 w-4 animate-spin" />}
              {isCreate ? "Add vehicle" : "Save changes"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
