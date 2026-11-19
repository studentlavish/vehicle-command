import React, { useRef, useState, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import api, { errorMessage } from "@/lib/api";
import { toast } from "sonner";
import SectionCard from "@/components/common/SectionCard";
import StatusBadge from "@/components/common/StatusBadge";
import { ScanLine, Upload, Sparkles, Loader2, LogIn as LogInIcon, LogOut as LogOutIcon, CheckCircle2, XCircle, Camera, ImageOff, X } from "lucide-react";
import { cn } from "@/lib/utils";

export default function PlateScanCard({ onScanComplete }) {
  const fileRef = useRef(null);
  const [preview, setPreview] = useState(null);
  const [b64, setB64] = useState("");
  const [scanning, setScanning] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [result, setResult] = useState(null);
  const [action, setAction] = useState("entry");
  const [camera, setCamera] = useState("CAM 01");

  const readFile = useCallback((file) => {
    if (!file) return;
    if (!file.type.startsWith("image/")) { toast.error("Please choose an image file"); return; }
    if (file.size > 10 * 1024 * 1024) { toast.error("Image is larger than 10 MB"); return; }
    const reader = new FileReader();
    reader.onload = () => {
      const url = reader.result;
      setPreview(url);
      setB64(typeof url === "string" && url.includes(",") ? url.split(",")[1] : url);
      setResult(null);
    };
    reader.readAsDataURL(file);
  }, []);

  const scan = async () => {
    if (!b64) return;
    setScanning(true);
    setResult(null);
    try {
      const { data } = await api.post("/vehicles/scan-plate", { image_base64: b64, auto_action: action, camera });
      setResult(data);
      if (data.plate) {
        toast.success(`Plate detected: ${data.plate}`, { description: data.action?.type ? `${data.action.type === "entry" ? "Entry" : "Exit"} recorded` : undefined });
        onScanComplete?.(data);
      } else {
        toast.warning("No plate detected", { description: "Try a clearer, closer photo." });
      }
    } catch (e) {
      toast.error("Scan failed", { description: errorMessage(e) });
    } finally {
      setScanning(false);
    }
  };

  const reset = () => { setPreview(null); setB64(""); setResult(null); if (fileRef.current) fileRef.current.value = ""; };

  return (
    <SectionCard
      icon={ScanLine}
      title="Number plate recognition"
      description="Upload a photo to read the plate and log an entry or exit"
      actions={<StatusBadge tone="primary">AI</StatusBadge>}
      data-testid="plate-scan-card"
    >
      <div className="grid gap-5 md:grid-cols-2">
        <div>
          <div
            role="button"
            tabIndex={0}
            onClick={() => fileRef.current?.click()}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && fileRef.current?.click()}
            onDrop={(e) => { e.preventDefault(); setDragging(false); readFile(e.dataTransfer.files?.[0]); }}
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            className={cn(
              "relative flex aspect-video cursor-pointer items-center justify-center overflow-hidden rounded-lg border-2 border-dashed transition-colors focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-primary/25",
              dragging ? "border-primary bg-primary/5" : "border-border bg-muted/40 hover:border-primary/50 hover:bg-primary/5",
            )}
            data-testid="plate-scan-dropzone"
          >
            {preview ? (
              <>
                <img src={preview} alt="Selected vehicle" className="h-full w-full object-cover" />
                {scanning && (
                  <div className="absolute inset-0 flex items-center justify-center bg-white/60 backdrop-blur-[1px]">
                    <div className="flex items-center gap-2 rounded-full bg-card px-3 py-1.5 text-sm font-medium shadow-pop">
                      <Loader2 className="h-4 w-4 animate-spin text-primary" /> Reading plate…
                    </div>
                  </div>
                )}
                {!scanning && (
                  <button type="button" onClick={(e) => { e.stopPropagation(); reset(); }} className="absolute right-2 top-2 rounded-full bg-card/90 p-1.5 shadow hover:bg-card" aria-label="Remove image" data-testid="plate-scan-reset">
                    <X className="h-4 w-4" />
                  </button>
                )}
              </>
            ) : (
              <div className="px-4 text-center">
                <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-card shadow-sm">
                  <Upload className="h-5 w-5 text-primary" />
                </div>
                <div className="mt-3 text-sm font-medium">Click to upload or drag a photo here</div>
                <div className="mt-0.5 text-xs text-muted-foreground">JPEG, PNG or WebP · up to 10 MB</div>
              </div>
            )}
            <input ref={fileRef} type="file" accept="image/*" onChange={(e) => readFile(e.target.files?.[0])} className="hidden" data-testid="plate-scan-file-input" />
          </div>
        </div>

        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="field-label">Action</label>
              <Select value={action} onValueChange={setAction}>
                <SelectTrigger data-testid="plate-scan-action"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">Detect only</SelectItem>
                  <SelectItem value="entry">Record entry</SelectItem>
                  <SelectItem value="exit">Record exit</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="field-label">Camera</label>
              <Input value={camera} onChange={(e) => setCamera(e.target.value)} className="font-mono" data-testid="plate-scan-camera" />
            </div>
          </div>

          <Button onClick={scan} disabled={scanning || !b64} className="w-full" data-testid="plate-scan-btn">
            {scanning ? <><Loader2 className="h-4 w-4 animate-spin" /> Analyzing…</> : <><Sparkles className="h-4 w-4" /> Scan plate</>}
          </Button>

          {result ? (
            <div className={cn("rounded-lg border p-4", result.plate ? "border-success/25 bg-success/5" : "border-destructive/20 bg-destructive/5")} data-testid="plate-scan-result">
              {result.plate ? (
                <div className="space-y-2.5">
                  <div className="flex items-center gap-1.5 text-xs font-semibold text-success"><CheckCircle2 className="h-4 w-4" /> Plate detected</div>
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="plate text-lg">{result.plate}</div>
                    <StatusBadge tone={result.confidence === "medium" ? "warning" : "danger"}>{result.confidence} confidence</StatusBadge>
                  </div>
                  {result.corrected && (
                    <div className="text-xs text-warning">Look-alike characters were corrected (e.g. O→0). Check it against the photo.</div>
                  )}
                  {result.action?.type && (
                    <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                      {result.action.type === "entry" ? <LogInIcon className="h-4 w-4 text-primary" /> : <LogOutIcon className="h-4 w-4 text-info" />}
                      {result.action.type === "entry" ? "Entry" : "Exit"} recorded
                      {result.action.session?.entry_camera && (
                        <span className="inline-flex items-center gap-1 font-mono text-xs"><Camera className="h-3 w-3" />{result.action.session.entry_camera}</span>
                      )}
                    </div>
                  )}
                  {result.action?.error && <div className="text-xs text-warning">{result.action.error}</div>}
                </div>
              ) : (
                <div className="space-y-1">
                  <div className="flex items-center gap-1.5 text-xs font-semibold text-destructive"><XCircle className="h-4 w-4" /> No plate found</div>
                  <div className="text-sm text-muted-foreground">{result.reason || "Try a sharper, closer photo with the plate clearly visible."}</div>
                </div>
              )}
            </div>
          ) : (
            <div className="flex flex-1 items-center gap-3 rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
              <ImageOff className="h-5 w-5 shrink-0" />
              The detected plate and the action taken will appear here.
            </div>
          )}
        </div>
      </div>
    </SectionCard>
  );
}
