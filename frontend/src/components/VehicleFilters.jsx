import React from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Calendar } from "@/components/ui/calendar";
import { Search, CalendarIcon, X, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

export default function VehicleFilters({ q, setQ, status, setStatus, date, setDate, loading }) {
  const showClear = q || status !== "all" || date;
  return (
    <div className="flex flex-col gap-3 md:flex-row md:items-center">
      <div className="relative flex-1">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          data-testid="vehicles-search-input"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search by plate, owner, phone or customer ID…"
          className="pl-9 pr-9"
        />
        {loading && <Loader2 className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-muted-foreground" />}
      </div>

      <div className="flex gap-3">
        <Popover>
          <PopoverTrigger asChild>
            <Button variant="outline" className={cn("h-10 w-full justify-start font-normal md:w-48", !date && "text-muted-foreground")} data-testid="date-filter-btn">
              <CalendarIcon className="h-4 w-4" />
              {date ? new Date(date).toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" }) : "Any date"}
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-auto p-0" align="start">
            <Calendar mode="single" selected={date} onSelect={setDate} initialFocus />
          </PopoverContent>
        </Popover>

        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger className="w-full md:w-44" data-testid="status-filter">
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            <SelectItem value="inside">Inside</SelectItem>
            <SelectItem value="exited">Exited</SelectItem>
            <SelectItem value="pending">Pending</SelectItem>
          </SelectContent>
        </Select>

        {showClear && (
          <Button variant="ghost" className="h-10 shrink-0" onClick={() => { setQ(""); setStatus("all"); setDate(null); }} data-testid="clear-filters">
            <X className="h-4 w-4" /> Clear
          </Button>
        )}
      </div>
    </div>
  );
}
