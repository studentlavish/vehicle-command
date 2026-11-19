import React from "react";
import { Link } from "react-router-dom";
import { cn } from "@/lib/utils";

/** Number plate styled like a real plate; links to the vehicle profile. */
export default function VehiclePlate({ number, link = true, className, ...rest }) {
  if (!number) return <span className="text-muted-foreground">—</span>;
  const chip = <span className={cn("plate", link && "transition-colors group-hover:border-primary group-hover:text-primary", className)}>{number}</span>;
  if (!link) return chip;
  return (
    <Link to={`/vehicle/${encodeURIComponent(number)}`} className="group inline-flex" {...rest}>
      {chip}
    </Link>
  );
}
