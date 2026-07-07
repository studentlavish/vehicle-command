import React from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Calendar } from "@/components/ui/calendar";
import { Search, Filter, CalendarIcon, X } from "lucide-react";

export default function VehicleFilters({ q, setQ, status, setStatus, date, setDate }) {
  const showClear = q || status !== "all" || date;
  return (
    <div className="glass rounded-2xl p-4 grid gap-3 md:grid-cols-12 items-center">
      <div className="md:col-span-5 relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" />
        <Input
          data-testid="vehicles-search-input"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search vehicle number or owner..."
          className="pl-9 h-10 bg-white/5 border-white/10 text-white placeholder:text-slate-500"
        />
      </div>

      <div className="md:col-span-3">
        <Popover>
          <PopoverTrigger asChild>
            <Button variant="outline" className="w-full h-10 justify-start bg-white/5 border-white/10 text-white hover:bg-white/10" data-testid="date-filter-btn">
              <CalendarIcon className="h-4 w-4 mr-2 text-slate-400" />
              {date ? new Date(date).toLocaleDateString() : "Filter by date"}
            </Button>
          </PopoverTrigger>
          <PopoverContent className="p-0 bg-[#0F172A]/95 border-white/10" align="start">
            <Calendar mode="single" selected={date} onSelect={setDate} initialFocus />
          </PopoverContent>
        </Popover>
      </div>

      <div className="md:col-span-3">
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger className="h-10 bg-white/5 border-white/10 text-white" data-testid="status-filter">
            <Filter className="h-4 w-4 mr-2 text-slate-400" />
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent className="bg-[#0F172A]/95 border-white/10 text-white">
            <SelectItem value="all">All statuses</SelectItem>
            <SelectItem value="inside">Inside</SelectItem>
            <SelectItem value="exited">Exited</SelectItem>
            <SelectItem value="pending">Pending</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <div className="md:col-span-1 flex justify-end">
        {showClear && (
          <Button variant="ghost" onClick={() => { setQ(""); setStatus("all"); setDate(null); }} className="text-slate-400 hover:text-white" data-testid="clear-filters">
            <X className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  );
}
