import React from "react";
import { motion } from "framer-motion";

export default function VisitStatCard({ label, value, sub, icon: Icon, tone = "blue", delay = 0, testId }) {
  const tones = {
    blue: "from-blue-500/25 to-blue-500/0 text-blue-300",
    sky: "from-sky-500/25 to-sky-500/0 text-sky-300",
    emerald: "from-emerald-500/25 to-emerald-500/0 text-emerald-300",
    violet: "from-violet-500/25 to-violet-500/0 text-violet-300",
    amber: "from-amber-500/25 to-amber-500/0 text-amber-300",
  };
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay }}
      className="glass rounded-2xl p-5 relative overflow-hidden hover:-translate-y-0.5 transition-all duration-300"
      data-testid={testId}
    >
      <div className={`absolute inset-0 bg-gradient-to-br ${tones[tone].split(" ")[0]} ${tones[tone].split(" ")[1]} pointer-events-none`} />
      <div className="relative flex items-start justify-between">
        <div>
          <div className="text-[11px] uppercase tracking-widest text-slate-400">{label}</div>
          <div className="mt-2 text-3xl font-bold tracking-tight text-white leading-tight">{value}</div>
          {sub ? <div className={`mt-1 text-xs ${tones[tone].split(" ")[2]}`}>{sub}</div> : null}
        </div>
        <div className="h-10 w-10 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center">
          {Icon ? <Icon className={`h-5 w-5 ${tones[tone].split(" ")[2]}`} /> : null}
        </div>
      </div>
    </motion.div>
  );
}
