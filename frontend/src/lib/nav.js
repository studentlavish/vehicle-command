import {
  LayoutDashboard, Radio, Car, LogIn as LogInIcon, LogOut, BarChart3, FileBarChart,
  Settings as SettingsIcon, Users as UsersIcon, Search, ScanEye, Video,
} from "lucide-react";

const ALL = ["admin", "manager", "security"];
const MGMT = ["admin", "manager"];

/**
 * Sidebar + command palette navigation. `label` is the English name (also used for
 * stable data-testids); `key` is the i18n string shown to the user.
 */
export const NAV_GROUPS = [
  {
    label: "Overview",
    key: "nav.group.overview",
    items: [
      { to: "/", icon: LayoutDashboard, label: "Dashboard", key: "nav.dashboard", end: true, roles: ALL },
      { to: "/live", icon: Radio, label: "Live Monitoring", key: "nav.live", roles: ALL },
      { to: "/reviews", icon: ScanEye, label: "Plate Review", key: "nav.reviews", roles: ALL, badge: "reviews" },
      { to: "/search", icon: Search, label: "Search Vehicle", key: "nav.search", roles: ALL },
    ],
  },
  {
    label: "Records",
    key: "nav.group.records",
    items: [
      { to: "/vehicles", icon: Car, label: "Vehicle Records", key: "nav.vehicles", roles: ALL },
      { to: "/entry-history", icon: LogInIcon, label: "Entry History", key: "nav.entries", roles: MGMT },
      { to: "/exit-history", icon: LogOut, label: "Exit History", key: "nav.exits", roles: MGMT },
    ],
  },
  {
    label: "Insights",
    key: "nav.group.insights",
    items: [
      { to: "/analytics", icon: BarChart3, label: "Analytics", key: "nav.analytics", roles: MGMT },
      { to: "/reports", icon: FileBarChart, label: "Reports", key: "nav.reports", roles: MGMT },
    ],
  },
  {
    label: "Administration",
    key: "nav.group.admin",
    items: [
      { to: "/cameras", icon: Video, label: "Cameras", key: "nav.cameras", roles: MGMT },
      { to: "/users", icon: UsersIcon, label: "Users", key: "nav.users", roles: MGMT },
      { to: "/settings", icon: SettingsIcon, label: "Settings", key: "nav.settings", roles: ["admin"] },
    ],
  },
];

export function navForRole(role) {
  return NAV_GROUPS
    .map((g) => ({ ...g, items: g.items.filter((i) => i.roles.includes(role || "admin")) }))
    .filter((g) => g.items.length);
}
