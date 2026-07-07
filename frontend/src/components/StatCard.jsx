import React from "react";
import { motion } from "framer-motion";
import { ResponsiveContainer, LineChart, Line, AreaChart, Area, YAxis } from "recharts";
import { ArrowUpRight, ArrowDownRight } from "lucide-react";

export default function StatCard({ label, value, change, trend, icon: Icon, accent = "blue", testId, delay = 0 }) {
  const up = change >= 0;
  const gradient =
    accent === "green"
      ? "from-emerald-500/25 to-emerald-500/0"
      : accent === "amber"
      ? "from-amber-500/25 to-amber-500/0"
      : accent === "sky"
      ? "from-sky-500/25 to-sky-500/0"
      : accent === "violet"
      ? "from-violet-500/25 to-violet-500/0"
      : "from-blue-500/25 to-blue-500/0";
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay }}
      className="glass rounded-2xl p-5 relative overflow-hidden hover:-translate-y-0.5 transition-all duration-300"
      data-testid={testId}
    >
      <div className={`absolute inset-0 bg-gradient-to-br ${gradient} pointer-events-none`} />
      <div className="relative flex items-start justify-between">
        <div>
          <div className="text-xs uppercase tracking-widest text-slate-400">{label}</div>
          <div className="mt-2 text-3xl font-bold tracking-tight text-white">{value}</div>
          <div className={`mt-1.5 inline-flex items-center gap-1 text-xs font-medium ${up ? "text-emerald-400" : "text-rose-400"}`}>
            {up ? <ArrowUpRight className="h-3.5 w-3.5" /> : <ArrowDownRight className="h-3.5 w-3.5" />}
            {Math.abs(change)}% vs yesterday
          </div>
        </div>
        <div className="h-10 w-10 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center">
          {Icon ? <Icon className="h-5 w-5 text-blue-400" /> : null}
        </div>
      </div>

      <div className="relative mt-4 h-14 -mx-1">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={trend || []}>
            <defs>
              <linearGradient id={`grad-${testId}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#3B82F6" stopOpacity={0.5} />
                <stop offset="100%" stopColor="#3B82F6" stopOpacity={0} />
              </linearGradient>
            </defs>
            <YAxis hide domain={["dataMin", "dataMax + 1"]} />
            <Area type="monotone" dataKey="value" stroke="#60A5FA" strokeWidth={2} fill={`url(#grad-${testId})`} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </motion.div>
  );
}
