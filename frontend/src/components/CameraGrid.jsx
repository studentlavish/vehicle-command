import React, { useEffect, useRef, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import api, { wsUrl } from "@/lib/api";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";
import StatusBadge from "@/components/common/StatusBadge";
import { EmptyState } from "@/components/common/EmptyState";
import { Video, VideoOff, Loader2, Wifi, WifiOff, Maximize2, Settings2 } from "lucide-react";
import { cn } from "@/lib/utils";

const ROLE_BADGE = {
  entry: { label: "ENTRY", cls: "bg-emerald-600/80" },
  exit: { label: "EXIT", cls: "bg-sky-600/80" },
  both: { label: "ENTRY + EXIT", cls: "bg-violet-600/80" },
};

/** Live feeds only — cameras are added and configured on the Cameras page. */
export function CameraGrid() {
  const { user } = useAuth();
  const canManage = user?.role === "admin" || user?.role === "manager";
  const [cameras, setCameras] = useState({});
  const [loading, setLoading] = useState(true);

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
        {canManage && (
          <Button variant="outline" asChild data-testid="manage-cameras-link">
            <Link to="/cameras"><Settings2 className="h-4 w-4" /> Manage cameras</Link>
          </Button>
        )}
      </div>

      {loading ? (
        <div className="grid gap-4 lg:grid-cols-2">{[0, 1].map((i) => <Skeleton key={i} className="aspect-video rounded-xl" />)}</div>
      ) : list.length === 0 ? (
        <div className="surface" data-testid="camera-empty">
          <EmptyState
            icon={VideoOff}
            title="No cameras connected"
            description="Add a camera on the Cameras page, or run the local camera agent on a showroom PC — its cameras appear automatically."
            action={canManage && <Button variant="outline" asChild><Link to="/cameras"><Settings2 className="h-4 w-4" /> Manage cameras</Link></Button>}
          />
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {list.map((c) => <CameraTile key={c.camera_id} state={c} />)}
        </div>
      )}
    </section>
  );
}

function CameraTile({ state }) {
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
        {ROLE_BADGE[state.role] && (
          <div
            className={cn("absolute bottom-2 left-2 rounded-md px-2 py-1 font-mono text-[10px] font-semibold text-white backdrop-blur-sm", ROLE_BADGE[state.role].cls, state.enabled === false && "opacity-60")}
            title={state.configured ? undefined : "No role set yet — treated as entry + exit"}
            data-testid={`camera-role-badge-${state.camera_id}`}
          >
            {ROLE_BADGE[state.role].label}{!state.configured && " ?"}{state.enabled === false && " · INACTIVE"}
          </div>
        )}
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

export default CameraGrid;
