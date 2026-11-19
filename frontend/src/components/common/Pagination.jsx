import React from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";

export default function Pagination({ page, pageSize, total, count, hasPrev, hasNext, onPrev, onNext, testIdPrefix = "page" }) {
  if (!total) return null;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const from = (page - 1) * pageSize + 1;
  const to = (page - 1) * pageSize + count;
  return (
    <div className="flex flex-col gap-3 border-t px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between" data-testid={`${testIdPrefix}-pagination`}>
      <div className="text-muted-foreground">
        Showing <span className="font-medium text-foreground">{from.toLocaleString()}</span>–
        <span className="font-medium text-foreground">{to.toLocaleString()}</span> of{" "}
        <span className="font-medium text-foreground">{total.toLocaleString()}</span>
      </div>
      <div className="flex items-center gap-2">
        <Button size="sm" variant="outline" disabled={!hasPrev} onClick={onPrev} data-testid={`${testIdPrefix}-prev`}>
          <ChevronLeft className="h-4 w-4" /> Previous
        </Button>
        <span className="px-1 tabular-nums text-muted-foreground">
          {page} / {totalPages}
        </span>
        <Button size="sm" variant="outline" disabled={!hasNext} onClick={onNext} data-testid={`${testIdPrefix}-next`}>
          Next <ChevronRight className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
