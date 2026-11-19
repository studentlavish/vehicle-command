import React from "react";
import { cn } from "@/lib/utils";

const TONES = {
  success: "bg-success/10 text-success border-success/20",
  warning: "bg-warning/10 text-warning border-warning/25",
  danger: "bg-destructive/10 text-destructive border-destructive/20",
  info: "bg-info/10 text-info border-info/20",
  primary: "bg-primary/10 text-primary border-primary/20",
  violet: "bg-violet-50 text-violet-700 border-violet-200",
  neutral: "bg-muted text-muted-foreground border-border",
};

/** Maps every status string used by the API to a label + tone. */
const STATUS_MAP = {
  inside: { label: "Inside", tone: "success", dot: true },
  active: { label: "In progress", tone: "success", dot: true },
  exited: { label: "Exited", tone: "neutral" },
  completed: { label: "Completed", tone: "neutral" },
  pending: { label: "Pending", tone: "warning" },
  exit_missed: { label: "Exit not seen", tone: "warning" },
  entry_missed: { label: "Entry not seen", tone: "warning" },
  online: { label: "Online", tone: "success", dot: true },
  connecting: { label: "Connecting", tone: "warning" },
  reconnecting: { label: "Reconnecting", tone: "warning" },
  offline: { label: "Offline", tone: "danger" },
  stopped: { label: "Stopped", tone: "neutral" },
  inactive: { label: "Inactive", tone: "neutral" },
  admin: { label: "Admin", tone: "primary" },
  manager: { label: "Manager", tone: "violet" },
  security: { label: "Security", tone: "info" },
};

export default function StatusBadge({ status, label, tone, dot, className, children, ...rest }) {
  const preset = STATUS_MAP[String(status || "").toLowerCase()] || {};
  const t = tone || preset.tone || "neutral";
  const showDot = dot ?? preset.dot;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-xs font-medium",
        TONES[t],
        className,
      )}
      {...rest}
    >
      {showDot ? <span className="live-dot !h-1.5 !w-1.5" /> : null}
      {children || label || preset.label || status || "—"}
    </span>
  );
}

export { TONES };
