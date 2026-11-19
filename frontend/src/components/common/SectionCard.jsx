import React from "react";
import { cn } from "@/lib/utils";

/** White card with an optional header row (icon + title + description + actions). */
export default function SectionCard({
  icon: Icon, title, description, actions, children, className, bodyClassName, noPadding = false, ...rest
}) {
  const hasHeader = title || actions;
  return (
    <section className={cn("surface", className)} {...rest}>
      {hasHeader && (
        <div className="flex items-start justify-between gap-3 border-b px-5 py-4">
          <div className="flex items-start gap-3 min-w-0">
            {Icon ? (
              <div className="icon-tile-sm">
                <Icon className="h-4 w-4" />
              </div>
            ) : null}
            <div className="min-w-0">
              <h3 className="text-[15px] font-semibold leading-8">{title}</h3>
              {description ? <p className="-mt-1 text-xs text-muted-foreground">{description}</p> : null}
            </div>
          </div>
          {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
        </div>
      )}
      <div className={cn(noPadding ? "" : "p-5", bodyClassName)}>{children}</div>
    </section>
  );
}
