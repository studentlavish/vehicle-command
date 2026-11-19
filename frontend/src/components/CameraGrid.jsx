import React, { useEffect, useRef, useState, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import api, { errorMessage, wsUrl } from "@/lib/api";
import { toast } from "sonner";
import StatusBadge from "@/components/common/StatusBadge";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import { EmptyState } from "@/components/common/EmptyState";
import { Video, VideoOff, Plus, PowerOff, Loader2, Camera as CamIcon, Wifi, WifiOff, Maximize2, Info } from "lucide-react";
import { cn } from "@/lib/utils";

const PRESETS = [
  { key: "http", label: "Android IP Webcam", placeholder: "http://PHONE_IP:8080/video", hint: "Install the “IP Webcam” app on an Android phone and copy its IPv4 URL." },
  { key: "rtsp", label: "RTSP camera / NVR", placeholder: "rtsp://username:password@IP:554/Streaming/Channels/101", hint: "Standard RTSP — works with most IP cameras and NVRs." },
  { key: "usb", label: "USB camera", placeholder: "0", hint: "Index of a webcam attached to the machine running the backend (usually 0)." },
];

export function CameraGrid() {
  const [cameras, setCameras] = useState({});
  const [loading, setLoading] = useState(true);
  const [showConnect, setShowConnect] = useState(false);
  const [toDisconnect, setToDisconnect] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const { data } = await api.get("/cameras");
      setCameras(data || {});
    } catch { /* keep last known */ } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [refresh]);

  const disconnect = async () => {
    const id = toDisconnect;
    try {
      await api.post(`/cameras/${encodeURIComponent(id)}/disconnect`);
      toast.success(`${id} disconnected`);
      refresh();
    } catch (e) {
      toast.error("Couldn't disconnect camera", { description: errorMessage(e) });
    }
  };

  const list = Object.values(cameras);
  const online = list.filter((c) => c.status === "online").length;

  return (
    <section className="space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <div className="icon-tile-sm"><Video className="h-4 w-4" /></div>
          <div>
            <h3 className="text-[15px] font-semibold">Live cameras</h3>
            <p className="text-xs text-muted-foreground">{loading ? "Loading…" : `${online} of ${list.length} online · USB, IP and RTSP sources`}</p>
          </div>
        </div>
        <Button onClick={() => setShowConnect(true)} data-testid="camera-connect-open">
          <Plus className="h-4 w-4" /> Connect camera
        </Button>
      </div>

      {loading ? (
        <div className="grid gap-4 lg:grid-cols-2">{[0, 1].map((i) => <Skeleton key={i} className="aspect-video rounded-xl" />)}</div>
      ) : list.length === 0 ? (
        <div className="surface" data-testid="camera-empty">
          <EmptyState
            icon={VideoOff}
            title="No cameras connected"
            description="Connect a camera here, or run the local camera agent on a showroom PC — its cameras appear automatically."
            action={<Button variant="outline" onClick={() => setShowConnect(true)}><Plus className="h-4 w-4" /> Connect camera</Button>}
          />
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {list.map((c) => <CameraTile key={c.camera_id} state={c} onDisconnect={() => setToDisconnect(c.camera_id)} />)}
        </div>
      )}

      <ConnectCameraDialog open={showConnect} onOpenChange={setShowConnect} onConnected={refresh} />
      <ConfirmDialog
        open={!!toDisconnect}
        onOpenChange={(v) => !v && setToDisconnect(null)}
        title={`Disconnect ${toDisconnect}?`}
        description="The stream stops and the camera is removed from this grid. You can connect it again later."
        confirmLabel="Disconnect"
        onConfirm={disconnect}
      />
    </section>
  );
}

function CameraTile({ state, onDisconnect }) {
  const imgRef = useRef(null);
  const boxRef = useRef(null);
  const objectUrlRef = useRef(null);
  const [connected, setConnected] = useState(false);
  const [liveStatus, setLiveStatus] = useState(state);
  const [frames, setFrames] = useState(0);

  useEffect(() => { setLiveStatus(state); }, [state]);

  useEffect(() => {
    const url = wsUrl(`/api/ws/camera/${encodeURIComponent(state.camera_id)}`);
    let ws;
    let closed = false;
    let retry;
    const open = () => {
      ws = new WebSocket(url);
      ws.binaryType = "arraybuffer";
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retry = setTimeout(open, 3000);
      };
      ws.onerror = () => { try { ws.close(); } catch { /* ignore */ } };
      ws.onmessage = (ev) => {
        if (typeof ev.data === "string") {
          try {
            const msg = JSON.parse(ev.data);
            if (msg.type === "status" && msg.camera) setLiveStatus(msg.camera);
            if (msg.type === "error") toast.error(msg.message);
          } catch { /* ignore */ }
          return;
        }
        const blobUrl = URL.createObjectURL(new Blob([ev.data], { type: "image/jpeg" }));
        if (imgRef.current) imgRef.current.src = blobUrl;
        const prev = objectUrlRef.current;
        objectUrlRef.current = blobUrl;
        if (prev) setTimeout(() => URL.revokeObjectURL(prev), 100);
        setFrames((f) => f + 1);
      };
    };
    open();
    return () => {
      closed = true;
      clearTimeout(retry);
      try { ws && ws.close(); } catch { /* ignore */ }
      if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
    };
  }, [state.camera_id]);

  const isOnline = liveStatus.status === "online";
  const fullscreen = () => boxRef.current?.requestFullscreen?.();

  return (
    <div className="surface overflow-hidden" data-testid={`camera-tile-${state.camera_id}`}>
      <div className="flex items-center justify-between gap-3 border-b px-4 py-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">{liveStatus.label || liveStatus.camera_id}</div>
          <div className="truncate font-mono text-[11px] text-muted-foreground">{liveStatus.camera_id}</div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <StatusBadge status={liveStatus.status} data-testid={`camera-status-${state.camera_id}`} />
          <Button size="icon-sm" variant="ghost" onClick={fullscreen} aria-label="Full screen" disabled={frames === 0}><Maximize2 /></Button>
          <Button size="icon-sm" variant="ghost-danger" onClick={onDisconnect} aria-label="Disconnect" data-testid={`camera-disconnect-${state.camera_id}`}><PowerOff /></Button>
        </div>
      </div>

      <div ref={boxRef} className="relative aspect-video bg-slate-900">
        <img ref={imgRef} alt={liveStatus.label || liveStatus.camera_id} className={cn("h-full w-full object-contain", frames === 0 && "invisible")} data-testid={`camera-frame-${state.camera_id}`} />

        {frames === 0 && (
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="text-center">
              {isOnline ? <Loader2 className="mx-auto h-7 w-7 animate-spin text-white/70" /> : <VideoOff className="mx-auto h-7 w-7 text-white/50" />}
              <div className="mt-2 text-xs text-white/70">{isOnline ? "Waiting for first frame…" : `Camera ${liveStatus.status}`}</div>
              {liveStatus.error && <div className="mx-auto mt-1 max-w-xs px-4 text-[11px] text-red-300">{liveStatus.error}</div>}
            </div>
          </div>
        )}

        <div className="absolute left-2 top-2 flex items-center gap-1.5 rounded-md bg-black/50 px-2 py-1 font-mono text-[10px] text-white backdrop-blur-sm">
          <span className={cn("h-1.5 w-1.5 rounded-full", isOnline ? "animate-pulse bg-red-500" : "bg-slate-400")} />
          {isOnline ? "LIVE" : "IDLE"}
        </div>
        <div className="absolute right-2 top-2 flex items-center gap-1 rounded-md bg-black/50 px-2 py-1 text-[10px] text-white backdrop-blur-sm" title={connected ? "Stream connected" : "Stream disconnected"}>
          {connected ? <Wifi className="h-3 w-3 text-emerald-300" /> : <WifiOff className="h-3 w-3 text-red-300" />}
        </div>
      </div>

      <div className="grid grid-cols-3 divide-x border-t text-center text-xs">
        <div className="px-2 py-2.5">
          <div className="text-muted-foreground">Frames</div>
          <div className="font-medium tabular-nums">{(liveStatus.frames_captured || 0).toLocaleString()}</div>
        </div>
        <div className="px-2 py-2.5">
          <div className="text-muted-foreground">Viewers</div>
          <div className="font-medium tabular-nums">{liveStatus.subscribers ?? 0}</div>
        </div>
        <div className="px-2 py-2.5">
          <div className="text-muted-foreground">Last frame</div>
          <div className="font-medium tabular-nums">{liveStatus.last_frame_at ? new Date(liveStatus.last_frame_at).toLocaleTimeString() : "—"}</div>
        </div>
      </div>
    </div>
  );
}

function ConnectCameraDialog({ open, onOpenChange, onConnected }) {
  const [type, setType] = useState("http");
  const [id, setId] = useState("");
  const [label, setLabel] = useState("");
  const [source, setSource] = useState("");
  const [busy, setBusy] = useState(false);
  const preset = PRESETS.find((p) => p.key === type) || PRESETS[0];

  const submit = async (e) => {
    e.preventDefault();
    const idTrim = id.trim().toUpperCase();
    if (!idTrim || !source.trim()) {
      toast.error("Camera ID and source are required");
      return;
    }
    setBusy(true);
    try {
      await api.post("/cameras/connect", { camera_id: idTrim, source: source.trim(), label: label.trim() });
      toast.success(`Connecting ${idTrim}…`, { description: "Status updates in real time." });
      onConnected?.();
      onOpenChange(false);
      setSource(""); setId(""); setLabel("");
    } catch (err) {
      toast.error("Couldn't connect camera", { description: errorMessage(err) });
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
                <DialogTitle>Connect a camera</DialogTitle>
                <DialogDescription>Stream a camera the server can reach directly.</DialogDescription>
              </div>
            </div>
          </DialogHeader>

          <div className="flex items-start gap-2 rounded-lg bg-info/10 px-3 py-2.5 text-xs leading-relaxed text-info">
            <Info className="mt-px h-3.5 w-3.5 shrink-0" />
            <span>Cameras on the showroom network that are streamed by the <span className="font-semibold">local camera agent</span> appear automatically — you don't need to add them here.</span>
          </div>

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
              <Input value={id} onChange={(e) => setId(e.target.value.toUpperCase())} className="font-mono uppercase" placeholder="GATE-01" data-testid="camera-id-input" />
            </div>
            <div className="col-span-2">
              <label className="field-label">Display name</label>
              <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Front gate" data-testid="camera-label-input" />
            </div>
            <div className="col-span-2">
              <label className="field-label">Source URL <span className="text-destructive">*</span></label>
              <Input value={source} onChange={(e) => setSource(e.target.value)} className="font-mono text-[13px]" placeholder={preset.placeholder} data-testid="camera-source-input" />
              <p className="mt-1.5 text-xs text-muted-foreground">{preset.hint}</p>
            </div>
          </div>

          <DialogFooter className="gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>Cancel</Button>
            <Button type="submit" disabled={busy} data-testid="camera-connect-submit">
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />} Connect
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default CameraGrid;
