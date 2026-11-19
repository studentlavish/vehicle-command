import React from "react";
import { cn } from "@/lib/utils";

/** Standard page title block: icon, title, description and right-side actions. */
export default function PageHeader({ icon: Icon, title, description, actions, children, className }) {
  return (
    <div className={cn("flex flex-col gap-4 md:flex-row md:items-center md:justify-between", className)}>
      <div className="flex items-start gap-3 min-w-0">
        {Icon ? (
          <div className="icon-tile hidden sm:inline-flex">
            <Icon className="h-5 w-5" />
          </div>
        ) : null}
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold md:text-[28px] leading-tight">{title}</h1>
          {description ? <p className="mt-1 text-sm text-muted-foreground">{description}</p> : null}
          {children}
        </div>
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}
