import React, { useEffect, useRef, useState, useCallback } from "react";
import { motion } from "framer-motion";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import api from "@/lib/api";
import { toast } from "sonner";
import { Video, VideoOff, Plus, PowerOff, ScanLine, Loader2, Camera as CamIcon, Wifi, WifiOff } from "lucide-react";

const PRESETS = [
  { key: "usb", label: "USB Camera", placeholder: "0", hint: "cv2.VideoCapture(0). Only works when the backend runs on a machine with a physical webcam." },
  { key: "http", label: "Android IP Webcam", placeholder: "http://PHONE_IP:8080/video", hint: "Install the ‘IP Webcam’ app on your Android phone and copy its IPv4 URL." },
  { key: "rtsp", label: "RTSP Camera", placeholder: "rtsp://username:password@IP:554/Streaming/Channels/101", hint: "Standard RTSP over TCP/UDP — most IP cameras and NVRs." },
];

const STATUS_COLOR = {
  online: "bg-emerald-500/15 text-emerald-300 border-emerald-500/25",
  connecting: "bg-amber-500/15 text-amber-300 border-amber-500/25",
  reconnecting: "bg-amber-500/15 text-amber-300 border-amber-500/25",
  offline: "bg-rose-500/15 text-rose-300 border-rose-500/25",
  stopped: "bg-slate-500/15 text-slate-300 border-slate-500/25",
};

function toWsUrl(path) {
  const base = process.env.REACT_APP_BACKEND_URL;
  const wsBase = base.replace(/^http/, "ws");
  return `${wsBase}${path}`;
}

export function CameraGrid() {
  const [cameras, setCameras] = useState({});
  const [loading, setLoading] = useState(true);
  const [showConnect, setShowConnect] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const { data } = await api.get("/cameras");
      setCameras(data);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [refresh]);

  const disconnect = async (id) => {
    try {
      await api.post(`/cameras/${encodeURIComponent(id)}/disconnect`);
      toast.success(`${id} disconnected`);
      refresh();
    } catch (e) {
      toast.error("Failed", { description: e.message });
    }
  };

  const list = Object.values(cameras);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Video className="h-4 w-4 text-blue-400" />
          <h3 className="font-semibold">Live Cameras</h3>
          <Badge className="bg-blue-500/15 text-blue-300 border border-blue-500/30 text-[10px]">USB · IP · RTSP</Badge>
        </div>
        <Button size="sm" onClick={() => setShowConnect(true)} className="bg-blue-600 hover:bg-blue-700" data-testid="camera-connect-open">
          <Plus className="h-4 w-4 mr-2" /> Connect Camera
        </Button>
      </div>

      {loading ? (
        <div className="glass rounded-2xl p-10 text-center text-slate-400 text-sm">Loading cameras…</div>
      ) : list.length === 0 ? (
        <div className="glass rounded-2xl p-10 text-center text-slate-400 text-sm" data-testid="camera-empty">
          No cameras connected yet. Click <span className="text-blue-300">Connect Camera</span> to add your first source.
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {list.map((c) => (
            <CameraTile key={c.camera_id} state={c} onDisconnect={() => disconnect(c.camera_id)} />
          ))}
        </div>
      )}

      <ConnectCameraDialog open={showConnect} onOpenChange={setShowConnect} onConnected={refresh} />
    </div>
  );
}

function CameraTile({ state, onDisconnect }) {
  const imgRef = useRef(null);
  const wsRef = useRef(null);
  const objectUrlRef = useRef(null);
  const [connected, setConnected] = useState(false);
  const [liveStatus, setLiveStatus] = useState(state);
  const [frames, setFrames] = useState(0);

  useEffect(() => {
    setLiveStatus(state);
  }, [state]);

  useEffect(() => {
    // Auth flows through httpOnly cookies — no token in localStorage.
    const url = toWsUrl(`/api/ws/camera/${encodeURIComponent(state.camera_id)}`);
    let ws;
    let closed = false;
    const open = () => {
      ws = new WebSocket(url);
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!closed) setTimeout(open, 3000); // auto-reconnect WS
      };
      ws.onerror = (err) => {
        if (process.env.NODE_ENV === "development") console.error("[camera ws] error", err);
        try { ws.close(); } catch (e) {
          if (process.env.NODE_ENV === "development") console.error("[camera ws] close after error failed", e);
        }
      };
      ws.onmessage = (ev) => {
        if (typeof ev.data === "string") {
          try {
            const msg = JSON.parse(ev.data);
            if (msg.type === "status" && msg.camera) setLiveStatus(msg.camera);
            if (msg.type === "error") toast.error(msg.message);
          } catch (e) {
            if (process.env.NODE_ENV === "development") console.error("[camera ws] bad json", e);
          }
          return;
        }
        const blob = new Blob([ev.data], { type: "image/jpeg" });
        const url = URL.createObjectURL(blob);
        if (imgRef.current) imgRef.current.src = url;
        const prev = objectUrlRef.current;
        objectUrlRef.current = url;
        if (prev) setTimeout(() => URL.revokeObjectURL(prev), 100);
        setFrames((f) => f + 1);
      };
    };
    open();
    return () => {
      closed = true;
      try { ws && ws.close(); } catch (e) {
        if (process.env.NODE_ENV === "development") console.error("[camera ws] close on unmount failed", e);
      }
      if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
    };
  }, [state.camera_id]);

  const isOnline = liveStatus.status === "online";

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="glass rounded-2xl p-4"
      data-testid={`camera-tile-${state.camera_id}`}
    >
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className="font-semibold">{liveStatus.label || liveStatus.camera_id}</div>
          <div className="text-[11px] text-slate-400 font-mono truncate max-w-[300px]">{liveStatus.source}</div>
        </div>
        <div className="flex items-center gap-2">
          <Badge className={`${STATUS_COLOR[liveStatus.status] || STATUS_COLOR.offline} border capitalize`} data-testid={`camera-status-${state.camera_id}`}>
            {isOnline ? <span className="pulse-dot text-emerald-400 mr-1">Online</span> : liveStatus.status}
          </Badge>
          <Button size="icon" variant="ghost" onClick={onDisconnect} className="h-8 w-8 text-rose-400 hover:bg-rose-500/10" data-testid={`camera-disconnect-${state.camera_id}`}>
            <PowerOff className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <div className="relative aspect-video rounded-xl overflow-hidden border border-white/10 bg-slate-900">
        {/* Video image */}
        <img ref={imgRef} alt={liveStatus.label} className="w-full h-full object-cover" data-testid={`camera-frame-${state.camera_id}`} />

        {/* Placeholder overlay when no frames yet */}
        {frames === 0 && (
          <div className="absolute inset-0 flex items-center justify-center bg-gradient-to-br from-slate-800 to-slate-900">
            <div className="text-center">
              {liveStatus.status === "online" ? (
                <Loader2 className="h-8 w-8 text-blue-400 animate-spin mx-auto" />
              ) : (
                <VideoOff className="h-8 w-8 text-slate-500 mx-auto" />
              )}
              <div className="text-xs text-slate-400 mt-2 capitalize">
                {liveStatus.status === "online" ? "Waiting for first frame…" : liveStatus.status}
              </div>
              {liveStatus.error && <div className="text-[10px] text-rose-300 mt-1 max-w-xs">{liveStatus.error}</div>}
            </div>
          </div>
        )}

        {/* HUD overlay */}
        <div className="absolute top-2 left-2 flex items-center gap-2 text-[11px] font-mono">
          <span className={`h-2 w-2 rounded-full ${isOnline ? "bg-red-500 animate-pulse" : "bg-slate-500"}`} />
          <span className="text-white/90">{isOnline ? "REC" : "IDLE"} · {liveStatus.camera_id}</span>
        </div>
        <div className="absolute top-2 right-2 text-[10px] font-mono text-white/70">
          {connected ? <Wifi className="h-3.5 w-3.5 inline text-blue-400" /> : <WifiOff className="h-3.5 w-3.5 inline text-rose-400" />}
          <span className="ml-1">WS</span>
        </div>
        <div className="absolute bottom-2 left-2 text-[10px] font-mono text-white/70">
          Frames: {liveStatus.frames_captured || 0} · Streamed: {frames}
        </div>
        <div className="absolute bottom-2 right-2 text-[10px] font-mono text-white/70">
          <ScanLine className="h-3 w-3 inline mr-1 text-blue-400" /> JPEG · WS
        </div>
      </div>

      <div className="mt-3 grid grid-cols-3 gap-2 text-[11px]">
        <div className="glass-strong rounded-lg p-2 text-center">
          <div className="text-slate-400">Status</div>
          <div className="font-mono capitalize">{liveStatus.status}</div>
        </div>
        <div className="glass-strong rounded-lg p-2 text-center">
          <div className="text-slate-400">Subscribers</div>
          <div className="font-mono">{liveStatus.subscribers ?? 0}</div>
        </div>
        <div className="glass-strong rounded-lg p-2 text-center">
          <div className="text-slate-400">Last Frame</div>
          <div className="font-mono">
            {liveStatus.last_frame_at ? new Date(liveStatus.last_frame_at).toLocaleTimeString() : "—"}
          </div>
        </div>
      </div>
    </motion.div>
  );
}

function ConnectCameraDialog({ open, onOpenChange, onConnected }) {
  const [type, setType] = useState("http");
  const [id, setId] = useState("cam-01");
  const [label, setLabel] = useState("Front Gate");
  const [source, setSource] = useState("");
  const [busy, setBusy] = useState(false);

  const preset = PRESETS.find((p) => p.key === type) || PRESETS[0];

  const submit = async () => {
    if (!id.trim() || !source.trim()) {
      toast.error("Camera ID and Source are required");
      return;
    }
    setBusy(true);
    try {
      await api.post("/cameras/connect", { camera_id: id.trim(), source: source.trim(), label: label.trim() });
      toast.success(`Connecting ${id}…`, { description: "Status updates in real-time." });
      onConnected?.();
      onOpenChange(false);
      setSource("");
    } catch (e) {
      toast.error("Connect failed", { description: e.response?.data?.detail || e.message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="bg-[#0F172A]/95 border-white/10 text-white max-w-lg" data-testid="camera-connect-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2"><CamIcon className="h-5 w-5 text-blue-400" /> Connect a Camera</DialogTitle>
        </DialogHeader>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label className="text-slate-300 text-[10px] uppercase tracking-widest">Camera Type</Label>
            <Select value={type} onValueChange={(v) => { setType(v); setSource(""); }}>
              <SelectTrigger className="mt-1 bg-white/5 border-white/10 text-white" data-testid="camera-type-select">
                <SelectValue />
              </SelectTrigger>
              <SelectContent className="bg-[#0F172A]/95 border-white/10 text-white">
                {PRESETS.map((p) => <SelectItem key={p.key} value={p.key}>{p.label}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-slate-300 text-[10px] uppercase tracking-widest">Camera ID</Label>
            <Input value={id} onChange={(e) => setId(e.target.value)} className="mt-1 bg-white/5 border-white/10 text-white font-mono" placeholder="cam-01" data-testid="camera-id-input" />
          </div>
          <div className="col-span-2">
            <Label className="text-slate-300 text-[10px] uppercase tracking-widest">Label (display name)</Label>
            <Input value={label} onChange={(e) => setLabel(e.target.value)} className="mt-1 bg-white/5 border-white/10 text-white" placeholder="Front Gate" data-testid="camera-label-input" />
          </div>
          <div className="col-span-2">
            <Label className="text-slate-300 text-[10px] uppercase tracking-widest">Source</Label>
            <Input value={source} onChange={(e) => setSource(e.target.value)} className="mt-1 bg-white/5 border-white/10 text-white font-mono" placeholder={preset.placeholder} data-testid="camera-source-input" />
            <div className="text-[11px] text-slate-500 mt-2">{preset.hint}</div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} className="text-slate-300">Cancel</Button>
          <Button onClick={submit} disabled={busy} className="bg-blue-600 hover:bg-blue-700" data-testid="camera-connect-submit">
            {busy ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Plus className="h-4 w-4 mr-2" />} Connect
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default CameraGrid;
