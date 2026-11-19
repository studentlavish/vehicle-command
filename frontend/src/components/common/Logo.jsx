import React from "react";
import { Car } from "lucide-react";
import { cn } from "@/lib/utils";

export default function Logo({ size = "md", showText = true, className }) {
  const box = size === "lg" ? "h-11 w-11 rounded-xl" : size === "sm" ? "h-8 w-8 rounded-lg" : "h-9 w-9 rounded-lg";
  const icon = size === "lg" ? "h-5 w-5" : "h-4 w-4";
  return (
    <div className={cn("flex items-center gap-3", className)}>
      <div className={cn(box, "flex items-center justify-center bg-primary text-primary-foreground shadow-sm shadow-primary/30")}>
        <Car className={icon} strokeWidth={2.25} />
      </div>
      {showText && (
        <div className="leading-tight">
          <div className={cn("font-semibold text-foreground", size === "lg" ? "text-base" : "text-sm")}>Vashu Hyundai</div>
          <div className="text-[11px] text-muted-foreground">Vehicle Management</div>
        </div>
      )}
    </div>
  );
}
