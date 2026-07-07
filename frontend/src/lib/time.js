/**
 * Time & duration formatting helpers used across the visit-session UI.
 */

export function formatDuration(seconds) {
  if (seconds == null || seconds <= 0) return "—";
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h && m) return `${h} Hour${h > 1 ? "s" : ""} ${m} Minute${m !== 1 ? "s" : ""}`;
  if (h) return `${h} Hour${h > 1 ? "s" : ""}`;
  if (m) return `${m} Minute${m !== 1 ? "s" : ""}`;
  return `${sec} Second${sec !== 1 ? "s" : ""}`;
}

export function formatDurationShort(seconds) {
  if (seconds == null || seconds <= 0) return "—";
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h && m) return `${h}h ${m}m`;
  if (h) return `${h}h`;
  return `${m}m`;
}

export function formatTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
  } catch {
    return iso;
  }
}

export function formatDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });
  } catch {
    return iso;
  }
}

export function formatDateTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  } catch {
    return iso;
  }
}
