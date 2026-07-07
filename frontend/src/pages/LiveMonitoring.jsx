import React from "react";
import { motion } from "framer-motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Camera, RadioTower, ScanLine, RefreshCw, Sparkles } from "lucide-react";

const CAMS = [
  { id: 1, name: "Entrance Gate", status: "online", location: "Main Gate" },
  { id: 2, name: "Exit Gate", status: "online", location: "Rear Gate" },
  { id: 3, name: "Showroom Floor", status: "offline", location: "Ground Floor" },
  { id: 4, name: "Parking Bay A", status: "online", location: "North Lot" },
];

export default function LiveMonitoring() {
  return (
    <div className="space-y-6" data-testid="live-page">
      <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-4">
        <div>
          <div className="inline-flex items-center gap-2 text-xs uppercase tracking-[0.25em] text-blue-400/80">
            <RadioTower className="h-3.5 w-3.5" /> Live Feed
          </div>
          <h1 className="mt-2 text-4xl md:text-5xl font-bold tracking-tight">Live Monitoring</h1>
          <p className="text-slate-400 mt-2 text-sm">Multi-camera CCTV grid with real-time number plate recognition placeholders.</p>
        </div>
        <Button variant="outline" className="bg-white/5 border-white/10 hover:bg-white/10 text-white" data-testid="refresh-feed">
          <RefreshCw className="h-4 w-4 mr-2" /> Refresh Feed
        </Button>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {CAMS.map((c, i) => (
          <motion.div
            key={c.id}
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.35, delay: i * 0.05 }}
            className="glass rounded-2xl p-4"
            data-testid={`cam-card-${c.id}`}
          >
            <div className="flex items-center justify-between mb-3">
              <div>
                <div className="font-semibold">{c.name}</div>
                <div className="text-[11px] text-slate-400">{c.location}</div>
              </div>
              {c.status === "online" ? (
                <Badge className="bg-emerald-500/15 text-emerald-300 border border-emerald-500/25">
                  <span className="pulse-dot text-emerald-400 mr-1">Online</span>
                </Badge>
              ) : (
                <Badge className="bg-rose-500/15 text-rose-300 border border-rose-500/25">Offline</Badge>
              )}
            </div>

            <div className="relative aspect-video rounded-xl overflow-hidden border border-white/10 bg-gradient-to-br from-slate-800 to-slate-900">
              <img
                src={`https://images.unsplash.com/photo-1485291571150-772bcfc10da5?auto=format&fit=crop&w=900&q=60&sig=${c.id}`}
                className="w-full h-full object-cover opacity-60"
                alt={c.name}
              />
              <div className="absolute inset-0 grid-bg opacity-40" />
              <div className="absolute inset-0 flex items-center justify-center">
                <div className="glass rounded-full p-4">
                  <Camera className="h-8 w-8 text-blue-400" />
                </div>
              </div>
              <div className="absolute top-3 left-3 flex items-center gap-2 text-[11px] font-mono">
                <span className={`h-2 w-2 rounded-full ${c.status === "online" ? "bg-red-500 animate-pulse" : "bg-slate-500"}`} />
                CAM {String(c.id).padStart(2, "0")} • {new Date().toLocaleTimeString()}
              </div>
              <div className="absolute bottom-3 right-3 text-[10px] font-mono text-slate-300/80">
                <ScanLine className="h-3.5 w-3.5 inline mr-1 text-blue-400" />1080p • H.265
              </div>
            </div>

            <div className="mt-3 grid grid-cols-3 gap-2 text-[11px]">
              <div className="glass-strong rounded-lg p-2 text-center">
                <div className="text-slate-400">FPS</div>
                <div className="font-mono">{c.status === "online" ? 30 : 0}</div>
              </div>
              <div className="glass-strong rounded-lg p-2 text-center">
                <div className="text-slate-400">Bitrate</div>
                <div className="font-mono">{c.status === "online" ? "4.2 Mbps" : "—"}</div>
              </div>
              <div className="glass-strong rounded-lg p-2 text-center">
                <div className="text-slate-400">Latency</div>
                <div className="font-mono">{c.status === "online" ? "142ms" : "—"}</div>
              </div>
            </div>
          </motion.div>
        ))}
      </div>

      <div className="glass rounded-2xl p-6 flex items-start gap-4">
        <div className="h-11 w-11 rounded-xl bg-blue-500/15 border border-blue-500/30 flex items-center justify-center shrink-0">
          <Sparkles className="h-5 w-5 text-blue-400" />
        </div>
        <div>
          <h3 className="font-semibold">Phase 2 — AI Number Plate Recognition</h3>
          <p className="text-sm text-slate-400 mt-1">
            RTSP/HTTP camera feeds will be routed through an OCR pipeline (Gemini Vision / OpenALPR) to auto-log
            entries and exits, with confidence-scored matches and WhatsApp / email alerts.
          </p>
        </div>
      </div>
    </div>
  );
}
