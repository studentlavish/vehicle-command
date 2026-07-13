import React, { useRef, useState, useCallback } from "react";
import { motion } from "framer-motion";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import api from "@/lib/api";
import { toast } from "sonner";
import { ScanLine, Upload, Sparkles, Loader2, LogIn as LogInIcon, LogOut as LogOutIcon, CheckCircle2, XCircle, Camera } from "lucide-react";

export default function PlateScanCard({ onScanComplete }) {
  const fileRef = useRef(null);
  const [preview, setPreview] = useState(null);
  const [b64, setB64] = useState("");
  const [scanning, setScanning] = useState(false);
  const [result, setResult] = useState(null);
  const [action, setAction] = useState("entry");
  const [camera, setCamera] = useState("CAM 01");

  const readFile = useCallback((file) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const url = reader.result;
      setPreview(url);
      const raw = typeof url === "string" && url.includes(",") ? url.split(",")[1] : url;
      setB64(raw);
      setResult(null);
    };
    reader.readAsDataURL(file);
  }, []);

  const onFile = (e) => readFile(e.target.files?.[0]);
  const onDrop = (e) => {
    e.preventDefault();
    readFile(e.dataTransfer.files?.[0]);
  };

  const scan = async () => {
    if (!b64) {
      toast.error("Choose an image first");
      return;
    }
    setScanning(true);
    setResult(null);
    try {
      const { data } = await api.post("/vehicles/scan-plate", {
        image_base64: b64,
        auto_action: action,
        camera,
      });
      setResult(data);
      if (data.plate) {
        toast.success(`Plate detected: ${data.plate}`, {
          description: data.action?.type ? `Auto ${data.action.type} recorded` : undefined,
        });
        onScanComplete?.(data);
      } else {
        toast.warning("No plate detected", { description: "Try a clearer photo." });
      }
    } catch (e) {
      toast.error("Scan failed", { description: e.response?.data?.detail || e.message });
    } finally {
      setScanning(false);
    }
  };

  const reset = () => { setPreview(null); setB64(""); setResult(null); if (fileRef.current) fileRef.current.value = ""; };

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="glass rounded-2xl p-5"
      data-testid="plate-scan-card"
    >
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <ScanLine className="h-4 w-4 text-blue-400" />
          <h3 className="font-semibold">AI Number Plate Recognition</h3>
          <Badge className="bg-blue-500/15 text-blue-300 border border-blue-500/30 text-[10px]">Gemini Vision</Badge>
        </div>
      </div>

      <div className="grid md:grid-cols-2 gap-4">
        {/* Upload zone */}
        <div>
          <div
            onClick={() => fileRef.current?.click()}
            onDrop={onDrop}
            onDragOver={(e) => e.preventDefault()}
            className="relative aspect-video rounded-xl border-2 border-dashed border-white/15 bg-white/[0.03] flex items-center justify-center cursor-pointer hover:border-blue-500/50 transition-colors overflow-hidden"
            data-testid="plate-scan-dropzone"
          >
            {preview ? (
              <img src={preview} alt="plate preview" className="w-full h-full object-cover" />
            ) : (
              <div className="text-center px-4">
                <Upload className="h-8 w-8 text-slate-500 mx-auto" />
                <div className="mt-2 text-sm text-slate-300">Click or drop a photo</div>
                <div className="text-[11px] text-slate-500">JPEG · PNG · WebP</div>
              </div>
            )}
            <input ref={fileRef} type="file" accept="image/*" onChange={onFile} className="hidden" data-testid="plate-scan-file-input" />
          </div>
          {preview && (
            <Button variant="ghost" size="sm" onClick={reset} className="mt-2 text-slate-400 hover:text-white" data-testid="plate-scan-reset">
              Choose different image
            </Button>
          )}
        </div>

        {/* Options + Result */}
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label className="text-slate-300 text-[10px] uppercase tracking-widest">Action</Label>
              <Select value={action} onValueChange={setAction}>
                <SelectTrigger className="mt-1 bg-white/5 border-white/10 text-white h-9" data-testid="plate-scan-action">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="bg-[#0F172A]/95 border-white/10 text-white">
                  <SelectItem value="none">Detect only</SelectItem>
                  <SelectItem value="entry">Auto Entry</SelectItem>
                  <SelectItem value="exit">Auto Exit</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="text-slate-300 text-[10px] uppercase tracking-widest">Camera</Label>
              <Input value={camera} onChange={(e) => setCamera(e.target.value)} className="mt-1 bg-white/5 border-white/10 text-white h-9 font-mono" data-testid="plate-scan-camera" />
            </div>
          </div>

          <Button
            onClick={scan}
            disabled={scanning || !b64}
            className="w-full bg-blue-600 hover:bg-blue-700"
            data-testid="plate-scan-btn"
          >
            {scanning ? (
              <><Loader2 className="h-4 w-4 mr-2 animate-spin" /> Analyzing…</>
            ) : (
              <><Sparkles className="h-4 w-4 mr-2" /> Scan Plate</>
            )}
          </Button>

          {/* Result panel */}
          {result && (
            <div className="glass-strong rounded-xl p-4 space-y-2" data-testid="plate-scan-result">
              {result.plate ? (
                <>
                  <div className="flex items-center gap-2 text-emerald-300">
                    <CheckCircle2 className="h-4 w-4" />
                    <span className="text-xs uppercase tracking-widest">Detected</span>
                  </div>
                  <div className="font-mono text-2xl text-white tracking-widest">{result.plate}</div>
                  {result.action?.type && (
                    <div className="flex items-center gap-2 text-sm text-slate-300">
                      {result.action.type === "entry" ? <LogInIcon className="h-4 w-4 text-blue-400" /> : <LogOutIcon className="h-4 w-4 text-sky-400" />}
                      Auto {result.action.type} recorded
                      {result.action.session?.entry_camera && (
                        <span className="text-[11px] text-slate-500 font-mono flex items-center gap-1"><Camera className="h-3 w-3" />{result.action.session.entry_camera}</span>
                      )}
                    </div>
                  )}
                  {result.action?.error && (
                    <div className="text-[11px] text-amber-300">{result.action.error}</div>
                  )}
                </>
              ) : (
                <>
                  <div className="flex items-center gap-2 text-rose-300">
                    <XCircle className="h-4 w-4" />
                    <span className="text-xs uppercase tracking-widest">No plate found</span>
                  </div>
                  <div className="text-xs text-slate-400">Try a sharper, closer photo where the plate is clearly visible.</div>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </motion.div>
  );
}
