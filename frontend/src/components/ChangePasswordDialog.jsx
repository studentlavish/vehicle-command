import React, { useState } from "react";
import api, { errorMessage } from "@/lib/api";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { KeyRound, Loader2 } from "lucide-react";
import { toast } from "sonner";

const MIN_LENGTH = 10;
const EMPTY = { current: "", next: "", confirm: "" };

export default function ChangePasswordDialog({ open, onOpenChange }) {
  const [form, setForm] = useState(EMPTY);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const close = (v) => {
    if (saving) return;
    if (!v) { setForm(EMPTY); setError(""); }
    onOpenChange(v);
  };

  const submit = async (e) => {
    e.preventDefault();
    if (form.next.length < MIN_LENGTH) return setError(`New password must be at least ${MIN_LENGTH} characters.`);
    if (form.next !== form.confirm) return setError("The new passwords don't match.");
    setSaving(true);
    setError("");
    try {
      await api.post("/auth/change-password", { current_password: form.current, new_password: form.next });
      toast.success("Password changed");
      setSaving(false);
      close(false);
    } catch (err) {
      setError(errorMessage(err));
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent data-testid="change-password-dialog">
        <form onSubmit={submit} className="space-y-5">
          <DialogHeader>
            <div className="flex items-center gap-3">
              <div className="icon-tile"><KeyRound className="h-5 w-5" /></div>
              <div className="text-left">
                <DialogTitle>Change password</DialogTitle>
                <DialogDescription>At least {MIN_LENGTH} characters. Avoid common passwords.</DialogDescription>
              </div>
            </div>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <label className="field-label">Current password</label>
              <Input type="password" autoComplete="current-password" value={form.current} onChange={(e) => setForm({ ...form, current: e.target.value })} autoFocus required />
            </div>
            <div>
              <label className="field-label">New password</label>
              <Input type="password" autoComplete="new-password" value={form.next} onChange={(e) => setForm({ ...form, next: e.target.value })} required />
            </div>
            <div>
              <label className="field-label">Confirm new password</label>
              <Input type="password" autoComplete="new-password" value={form.confirm} onChange={(e) => setForm({ ...form, confirm: e.target.value })} required />
            </div>
            {error && <p className="text-sm text-destructive" role="alert">{error}</p>}
          </div>
          <DialogFooter className="gap-2">
            <Button type="button" variant="outline" onClick={() => close(false)} disabled={saving}>Cancel</Button>
            <Button type="submit" disabled={saving}>
              {saving && <Loader2 className="h-4 w-4 animate-spin" />} Change password
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
