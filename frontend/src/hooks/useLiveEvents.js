import { useEffect, useRef, useState } from "react";
import { wsUrl } from "@/lib/api";

/**
 * Subscribes to the backend event bus (/api/ws/events) with auto-reconnect.
 * `onEvent(msg)` fires for every JSON message. Returns connection state.
 */
export default function useLiveEvents(onEvent) {
  const handler = useRef(onEvent);
  handler.current = onEvent;
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const url = wsUrl("/api/ws/events");
    if (!url) return undefined;
    let ws;
    let closed = false;
    let retry;
    const open = () => {
      ws = new WebSocket(url);
      ws.onopen = () => setConnected(true);
      ws.onmessage = (ev) => {
        let msg;
        try { msg = JSON.parse(ev.data); } catch { return; }
        handler.current?.(msg);
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retry = setTimeout(open, 3000);
      };
      ws.onerror = () => { try { ws.close(); } catch { /* ignore */ } };
    };
    open();
    return () => {
      closed = true;
      clearTimeout(retry);
      try { ws && ws.close(); } catch { /* ignore */ }
    };
  }, []);

  return connected;
}
