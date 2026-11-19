/**
 * Time & duration formatting helpers used across the UI.
 */

export function formatDuration(seconds) {
  if (seconds == null || seconds <= 0) return "—";
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h && m) return `${h} hr${h > 1 ? "s" : ""} ${m} min`;
  if (h) return `${h} hr${h > 1 ? "s" : ""}`;
  if (m) return `${m} min`;
  return `${sec} sec`;
}

export function formatDurationShort(seconds) {
  if (seconds == null || seconds <= 0) return "—";
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h && m) return `${h}h ${m}m`;
  if (h) return `${h}h`;
  if (m) return `${m}m`;
  return `${s}s`;
}

export function formatTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: true });
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
    return new Date(iso).toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: true });
  } catch {
    return iso;
  }
}

export function relativeAgo(iso) {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const s = Math.floor(Math.max(0, Date.now() - then) / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

/** Elapsed duration between entry and exit (or now, if still inside). */
export function elapsed(entry, exit) {
  if (!entry) return "—";
  const ms = (exit ? new Date(exit) : new Date()) - new Date(entry);
  if (Number.isNaN(ms) || ms < 0) return "—";
  return formatDurationShort(ms / 1000);
}

export function greeting(d = new Date()) {
  const h = d.getHours();
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  return "Good evening";
}

/** Local calendar date as YYYY-MM-DD (avoids the UTC shift of toISOString). */
export function toISODate(d) {
  if (!d) return undefined;
  return new Date(d).toLocaleDateString("en-CA");
}
