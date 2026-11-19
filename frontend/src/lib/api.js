import axios from "axios";

export const BACKEND_URL = (process.env.REACT_APP_BACKEND_URL || "").replace(/\/$/, "");
export const API = `${BACKEND_URL}/api`;

// Auth relies on httpOnly cookies set by the backend; no token is stored in JS.
const api = axios.create({
  baseURL: API,
  withCredentials: true,
});

export default api;

/** ws:// or wss:// URL for a backend path such as "/api/ws/events". */
export function wsUrl(path) {
  const base = BACKEND_URL || window.location.origin;
  return base.replace(/^http/, "ws") + path;
}

/** Absolute URL for a backend-served asset path (e.g. "/api/snapshots/x.jpg"). */
export function assetUrl(path) {
  if (!path) return "";
  if (/^(https?:|data:|blob:)/.test(path)) return path;
  return `${BACKEND_URL}${path}`;
}

export function formatApiError(detail) {
  if (detail == null) return "Something went wrong.";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail
      .map((e) => (e && typeof e.msg === "string" ? e.msg : JSON.stringify(e)))
      .join(" ");
  if (detail && typeof detail.msg === "string") return detail.msg;
  return String(detail);
}

/** Human-readable message from an axios error. */
export function errorMessage(err) {
  if (!err) return "Something went wrong.";
  if (err.response?.data?.detail) return formatApiError(err.response.data.detail);
  if (err.code === "ERR_NETWORK") return "Can't reach the server. Is the backend running?";
  return err.message || "Something went wrong.";
}

/** Trigger a browser download from an axios blob response. */
export function downloadBlob(res, filename) {
  const blob = new Blob([res.data]);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
