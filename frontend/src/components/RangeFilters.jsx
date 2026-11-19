import React from "react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Calendar } from "@/components/ui/calendar";
import { CalendarIcon } from "lucide-react";
import { cn } from "@/lib/utils";

const PRESETS = [
  { key: "today", label: "Today" },
  { key: "yesterday", label: "Yesterday" },
  { key: "7d", label: "7 days" },
  { key: "30d", label: "30 days" },
  { key: "180d", label: "180 days" },
];

const fmt = (d) => d.toLocaleDateString(undefined, { day: "2-digit", month: "short" });

/** Segmented date-range picker used on Reports and Vehicle profile. */
export default function RangeFilters({ value, onChange, customFrom, customTo, onCustom, className }) {
  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)} data-testid="range-filters">
      <div className="inline-flex flex-wrap rounded-lg border bg-muted p-1">
        {PRESETS.map((p) => {
          const active = value === p.key;
          return (
            <button
              key={p.key}
              type="button"
              onClick={() => onChange(p.key)}
              data-testid={`range-${p.key}`}
              className={cn(
                "rounded-md px-3 py-1.5 text-xs font-medium transition-all",
                active ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {p.label}
            </button>
          );
        })}
      </div>

      <Popover>
        <PopoverTrigger asChild>
          <Button
            size="sm"
            variant={value === "custom" ? "soft" : "outline"}
            className="h-9"
            data-testid="range-custom"
          >
            <CalendarIcon className="h-3.5 w-3.5" />
            {value === "custom" && customFrom && customTo ? `${fmt(customFrom)} – ${fmt(customTo)}` : "Custom range"}
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-auto p-3">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <div>
              <div className="eyebrow mb-1 px-1">From</div>
              <Calendar mode="single" selected={customFrom} onSelect={(d) => onCustom(d, customTo)} disabled={(d) => d > new Date()} />
            </div>
            <div>
              <div className="eyebrow mb-1 px-1">To</div>
              <Calendar mode="single" selected={customTo} onSelect={(d) => onCustom(customFrom, d)} disabled={(d) => d > new Date() || (customFrom && d < customFrom)} />
            </div>
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
}
