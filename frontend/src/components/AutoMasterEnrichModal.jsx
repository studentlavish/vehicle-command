import React, { useEffect, useState } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { CarFront, Sparkles } from "lucide-react";
import { toast } from "sonner";
import api from "@/lib/api";

/** Modal that prompts the operator to fill Owner Name + Phone the first time
 *  a brand-new plate is auto-detected. Queues new plates so operator can
 *  process them one at a time even if they arrive in bursts.
 */
export default function AutoMasterEnrichModal({ pending, onResolved }) {
  // pending: array of { vehicle_number, snapshot } waiting to be enriched
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const current = pending[0];

  useEffect(() => {
    setName(""); setPhone(""); setModel("");
  }, [current && current.vehicle_number]);

  if (!current) return null;

  const close = () => onResolved(current.vehicle_number);

  const save = async () => {
    if (!name.trim()) {
      toast.error("Owner name is required");
      return;
    }
    setBusy(true);
    try {
      await api.patch(`/vehicles/master/${encodeURIComponent(current.vehicle_number)}`, {
        owner_name: name.trim(),
        phone_number: phone.trim(),
        vehicle_model: model.trim() || undefined,
      });
      toast.success(`Saved ${current.vehicle_number}`, { description: name.trim() });
      close();
    } catch (e) {
      toast.error("Save failed", { description: e.response?.data?.detail || e.message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={!!current} onOpenChange={(o) => { if (!o) close(); }}>
      <DialogContent className="glass border-white/10 max-w-md" data-testid="enrich-modal">
        <DialogHeader>
          <div className="inline-flex items-center gap-2 text-xs uppercase tracking-[0.25em] text-blue-400/80">
            <Sparkles className="h-3.5 w-3.5" /> New Visitor Detected
          </div>
          <DialogTitle className="font-mono text-2xl mt-1">{current.vehicle_number}</DialogTitle>
          <DialogDescription className="text-slate-400">
            First time we've seen this plate — add the owner details so the visit is fully tagged.
            {pending.length > 1 ? ` · ${pending.length - 1} more waiting.` : ""}
          </DialogDescription>
        </DialogHeader>

        {current.snapshot ? (
          <div className="rounded-xl overflow-hidden border border-white/10 aspect-video bg-black/30">
            <img src={current.snapshot} alt={current.vehicle_number} className="w-full h-full object-cover" />
          </div>
        ) : (
          <div className="rounded-xl border border-white/10 aspect-video bg-black/30 flex items-center justify-center">
            <CarFront className="h-10 w-10 text-slate-600" />
          </div>
        )}

        <div className="grid gap-3">
          <div>
            <Label className="text-xs text-slate-400">Owner Name *</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Priya Kapoor"
              className="mt-1 bg-white/5 border-white/10"
              data-testid="enrich-owner-name"
              autoFocus
            />
          </div>
          <div>
            <Label className="text-xs text-slate-400">Phone Number</Label>
            <Input
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              placeholder="+91 98xxx xxxxx"
              className="mt-1 bg-white/5 border-white/10"
              data-testid="enrich-phone"
            />
          </div>
          <div>
            <Label className="text-xs text-slate-400">Vehicle Model (optional)</Label>
            <Input
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="Hyundai Creta"
              className="mt-1 bg-white/5 border-white/10"
              data-testid="enrich-model"
            />
          </div>
        </div>

        <DialogFooter className="gap-2">
          <Button
            variant="ghost"
            onClick={close}
            disabled={busy}
            className="text-slate-300 hover:bg-white/5"
            data-testid="enrich-skip"
          >
            Skip for now
          </Button>
          <Button
            onClick={save}
            disabled={busy}
            className="bg-blue-600 hover:bg-blue-500 text-white"
            data-testid="enrich-save"
          >
            {busy ? "Saving…" : "Save owner"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
