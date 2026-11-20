import React, { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import api, { errorMessage } from "@/lib/api";
import { toast } from "sonner";
import { Camera as CamIcon, Info, Loader2, Plus, Save } from "lucide-react";

export const CAMERA_ROLES = [
  { value: "entry", label: "Entry", hint: "Only records cars coming in" },
  { value: "exit", label: "Exit", hint: "Only records cars going out" },
  { value: "both", label: "Entry + exit", hint: "One camera for both directions" },
];

const PRESETS = [
  { key: "http", label: "Android IP Webcam", placeholder: "http://PHONE_IP:8080/video", hint: "Install the “IP Webcam” app on an Android phone and copy its IPv4 URL." },
  { key: "rtsp", label: "RTSP camera / NVR", placeholder: "rtsp://username:password@IP:554/Streaming/Channels/101", hint: "Standard RTSP — works with most IP cameras and NVRs." },
  { key: "usb", label: "USB camera", placeholder: "0", hint: "Index of a webcam attached to the machine running the backend (usually 0)." },
];

/**
 * Add a direct-URL camera, or change the source of an existing one.
 * `camera` (optional) pre-fills the form for editing; its ID is then fixed.
 */
export default function CameraFormDialog({ open, onOpenChange, onSaved, camera, canSetRole }) {
  const editing = !!camera;
  const [type, setType] = useState("http");
  const [id, setId] = useState("");
  const [label, setLabel] = useState("");
  const [source, setSource] = useState("");
  const [role, setRole] = useState("both");
  const [gate, setGate] = useState("");
  const [busy, setBusy] = useState(false);
  const preset = PRESETS.find((p) => p.key === type) || PRESETS[0];

  useEffect(() => {
    if (!open) return;
    setId(camera?.camera_id || "");
    setLabel(camera?.name || "");
    setRole(camera?.role || "both");
    setGate(camera?.gate || "");
    setType(["http", "rtsp", "usb"].includes(camera?.source_type) ? camera.source_type : "http");
    setSource("");
  }, [open, camera]);

  const submit = async (e) => {
    e.preventDefault();
    const idTrim = id.trim().toUpperCase();
    if (!idTrim || !source.trim()) {
      toast.error("Camera ID and source are required");
      return;
    }
    setBusy(true);
    try {
      await api.post("/cameras/connect", {
        camera_id: idTrim, source: source.trim(), label: label.trim(), gate: gate.trim(),
        ...(canSetRole ? { role } : {}),
      });
      toast.success(editing ? `${idTrim} updated` : `Connecting ${idTrim}…`, { description: "Status updates in real time." });
      onSaved?.();
      onOpenChange(false);
    } catch (err) {
      toast.error("Couldn't save camera", { description: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !busy && onOpenChange(v)}>
      <DialogContent className="max-w-lg" data-testid="camera-connect-dialog">
        <form onSubmit={submit} className="space-y-5">
          <DialogHeader>
            <div className="flex items-center gap-3">
              <div className="icon-tile"><CamIcon className="h-5 w-5" /></div>
              <div className="text-left">
                <DialogTitle>{editing ? `Change source of ${camera.camera_id}` : "Add a camera"}</DialogTitle>
                <DialogDescription>Stream a camera the server can reach directly.</DialogDescription>
              </div>
            </div>
          </DialogHeader>

          {!editing && (
            <div className="flex items-start gap-2 rounded-lg bg-info/10 px-3 py-2.5 text-xs leading-relaxed text-info">
              <Info className="mt-px h-3.5 w-3.5 shrink-0" />
              <span>Cameras streamed by the <span className="font-semibold">local camera agent</span> appear on this page automatically — you only need to set their role.</span>
            </div>
          )}

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="field-label">Camera type</label>
              <Select value={type} onValueChange={(v) => { setType(v); setSource(""); }}>
                <SelectTrigger data-testid="camera-type-select"><SelectValue /></SelectTrigger>
                <SelectContent>{PRESETS.map((p) => <SelectItem key={p.key} value={p.key}>{p.label}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div>
              <label className="field-label">Camera ID <span className="text-destructive">*</span></label>
              <Input value={id} disabled={editing} onChange={(e) => setId(e.target.value.toUpperCase())} className="font-mono uppercase" placeholder="GATE-01" data-testid="camera-id-input" />
            </div>
            <div className="col-span-2">
              <label className="field-label">Source URL <span className="text-destructive">*</span></label>
              <Input value={source} onChange={(e) => setSource(e.target.value)} className="font-mono text-[13px]" placeholder={editing && camera.source ? camera.source : preset.placeholder} data-testid="camera-source-input" />
              <p className="mt-1.5 text-xs text-muted-foreground">{preset.hint}</p>
            </div>
            {!editing && (
              <>
                <div className="col-span-2">
                  <label className="field-label">Display name</label>
                  <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Main gate — in" data-testid="camera-label-input" />
                </div>
                <div>
                  <label className="field-label">Role</label>
                  <Select value={role} onValueChange={setRole} disabled={!canSetRole}>
                    <SelectTrigger data-testid="camera-role-select"><SelectValue /></SelectTrigger>
                    <SelectContent>{CAMERA_ROLES.map((r) => <SelectItem key={r.value} value={r.value}>{r.label}</SelectItem>)}</SelectContent>
                  </Select>
                  {!canSetRole && <p className="mt-1.5 text-xs text-muted-foreground">An admin sets the role.</p>}
                </div>
                <div>
                  <label className="field-label">Gate</label>
                  <Input value={gate} onChange={(e) => setGate(e.target.value)} placeholder="e.g. Main" />
                </div>
              </>
            )}
          </div>

          <DialogFooter className="gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>Cancel</Button>
            <Button type="submit" disabled={busy} data-testid="camera-connect-submit">
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : editing ? <Save className="h-4 w-4" /> : <Plus className="h-4 w-4" />}
              {editing ? "Save" : "Add camera"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
