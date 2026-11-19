import React from "react";
import { Loader2 } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export function Spinner({ className }) {
  return <Loader2 className={cn("h-4 w-4 animate-spin", className)} />;
}

/** Full-area centred loader. */
export function PageLoader({ label = "Loading…", className }) {
  return (
    <div className={cn("flex min-h-[40vh] flex-col items-center justify-center gap-3 text-muted-foreground", className)}>
      <Loader2 className="h-6 w-6 animate-spin text-primary" />
      <span className="text-sm">{label}</span>
    </div>
  );
}

/** Skeleton rows for a .data-table body. */
export function TableSkeletonRows({ rows = 6, cols = 5 }) {
  return Array.from({ length: rows }).map((_, r) => (
    <tr key={`sk-${r}`} className="hover:bg-transparent">
      {Array.from({ length: cols }).map((__, c) => (
        <td key={c}>
          <Skeleton className={cn("h-4", c === 0 ? "w-24" : c === cols - 1 ? "w-12 ml-auto" : "w-full max-w-[140px]")} />
        </td>
      ))}
    </tr>
  ));
}

export function CardSkeleton({ className }) {
  return (
    <div className={cn("surface p-5", className)}>
      <div className="flex items-start justify-between">
        <div className="space-y-3">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-7 w-16" />
          <Skeleton className="h-3 w-20" />
        </div>
        <Skeleton className="h-10 w-10 rounded-lg" />
      </div>
    </div>
  );
}
