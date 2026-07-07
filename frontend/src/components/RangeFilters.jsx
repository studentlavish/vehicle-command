import React from "react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Calendar } from "@/components/ui/calendar";
import { CalendarIcon } from "lucide-react";

const PRESETS = [
  { key: "today", label: "Today" },
  { key: "yesterday", label: "Yesterday" },
  { key: "7d", label: "Last 7 Days" },
  { key: "30d", label: "Last 30 Days" },
  { key: "180d", label: "Last 180 Days" },
];

export default function RangeFilters({ value, onChange, customFrom, customTo, onCustom }) {
  return (
    <div className="glass rounded-2xl p-3 flex flex-wrap gap-2 items-center" data-testid="range-filters">
      {PRESETS.map((p) => {
        const active = value === p.key;
        return (
          <Button
            key={p.key}
            size="sm"
            variant="ghost"
            onClick={() => onChange(p.key)}
            data-testid={`range-${p.key}`}
            className={`h-9 rounded-full px-4 text-xs font-medium border transition-all ${
              active
                ? "bg-blue-600 text-white border-blue-500 shadow-lg shadow-blue-900/40"
                : "bg-white/5 text-slate-300 border-white/10 hover:bg-white/10"
            }`}
          >
            {p.label}
          </Button>
        );
      })}

      <Popover>
        <PopoverTrigger asChild>
          <Button
            size="sm"
            variant="ghost"
            data-testid="range-custom"
            className={`h-9 rounded-full px-4 text-xs font-medium border ${
              value === "custom"
                ? "bg-blue-600 text-white border-blue-500"
                : "bg-white/5 text-slate-300 border-white/10 hover:bg-white/10"
            }`}
          >
            <CalendarIcon className="h-3.5 w-3.5 mr-2" />
            {value === "custom" && customFrom && customTo
              ? `${customFrom.toLocaleDateString()} → ${customTo.toLocaleDateString()}`
              : "Custom Range"}
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="bg-[#0F172A]/95 border-white/10 text-white p-3 w-auto">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <div className="text-[10px] uppercase tracking-widest text-slate-400 mb-1">From</div>
              <Calendar mode="single" selected={customFrom} onSelect={(d) => onCustom(d, customTo)} />
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-widest text-slate-400 mb-1">To</div>
              <Calendar mode="single" selected={customTo} onSelect={(d) => onCustom(customFrom, d)} />
            </div>
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
}
