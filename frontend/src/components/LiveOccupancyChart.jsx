import React, { useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import { Car, TrendingUp } from "lucide-react";

/** Small live sparkline of cars-inside over the last ~2 hours.
 *  Updates every 30s (poll) + immediately on each live 'entry.recorded' bump. */
export default function LiveOccupancyChart({ liveValue }) {
  const [samples, setSamples] = useState([]); // {t, v}
  const lastLiveRef = useRef(null);

  // Poll cars-inside every 30s
  useEffect(() => {
    let cancelled = false;
    const push = (v) => setSamples((prev) => {
      const next = [...prev, { t: Date.now(), v: Number(v) || 0 }];
      // Keep ~2h of samples @ 30s = 240 points cap
      return next.slice(-240);
    });
    const tick = async () => {
      try {
        // Reuse api base URL from env — no import needed to keep this tiny.
        const base = process.env.REACT_APP_BACKEND_URL;
        const res = await fetch(`${base}/api/dashboard/stats`, { credentials: "include" });
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled) push(data?.cars_inside?.value ?? 0);
      } catch { /* ignore */ }
    };
    tick();
    const i = setInterval(tick, 30000);
    return () => { cancelled = true; clearInterval(i); };
  }, []);

  // React to live entry bump — pushes an extra point immediately.
  useEffect(() => {
    if (liveValue == null) return;
    if (lastLiveRef.current === liveValue) return;
    lastLiveRef.current = liveValue;
    setSamples((prev) => [...prev, { t: Date.now(), v: Number(liveValue) || 0 }].slice(-240));
  }, [liveValue]);

  const { path, area, current, peak, min } = useMemo(() => buildPath(samples), [samples]);

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: 0.18 }}
      className="glass rounded-2xl p-5"
      data-testid="live-occupancy-card"
    >
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <TrendingUp className="h-4 w-4 text-blue-400" />
          <h3 className="font-semibold">Live Occupancy</h3>
        </div>
        <div className="text-[11px] text-slate-400">Rolling · last 2h</div>
      </div>
      <div className="flex items-end justify-between">
        <div>
          <div className="text-4xl font-bold tracking-tight flex items-baseline gap-2" data-testid="occupancy-current">
            <Car className="h-6 w-6 text-emerald-400" />
            {current}
          </div>
          <div className="text-[11px] text-slate-400 mt-1">
            peak {peak} · low {min}
          </div>
        </div>
        <svg viewBox="0 0 240 64" className="w-2/3 h-16" preserveAspectRatio="none" data-testid="occupancy-spark">
          <defs>
            <linearGradient id="occ-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#22d3ee" stopOpacity="0.6" />
              <stop offset="100%" stopColor="#22d3ee" stopOpacity="0" />
            </linearGradient>
          </defs>
          {area ? <path d={area} fill="url(#occ-fill)" /> : null}
          {path ? <path d={path} fill="none" stroke="#22d3ee" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /> : null}
        </svg>
      </div>
    </motion.div>
  );
}

function buildPath(samples) {
  if (!samples || samples.length === 0) {
    return { path: "", area: "", current: 0, peak: 0, min: 0 };
  }
  const values = samples.map((s) => s.v);
  const peak = Math.max(...values);
  const min = Math.min(...values);
  const current = values[values.length - 1];
  const w = 240, h = 64, pad = 2;
  const n = samples.length;
  const dx = n === 1 ? 0 : (w - pad * 2) / (n - 1);
  const range = Math.max(1, peak - min);
  const y = (v) => h - pad - ((v - min) / range) * (h - pad * 2);
  const pts = samples.map((s, i) => `${pad + i * dx},${y(s.v).toFixed(1)}`);
  const path = "M" + pts.join(" L");
  const area = `${path} L${pad + (n - 1) * dx},${h} L${pad},${h} Z`;
  return { path, area, current, peak, min };
}
