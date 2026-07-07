import React, { useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import { useAuth } from "@/context/AuthContext";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  LayoutDashboard,
  Radio,
  Car,
  LogIn as LogInIcon,
  LogOut,
  BarChart3,
  FileBarChart,
  Settings as SettingsIcon,
  Users as UsersIcon,
  Search,
  Bell,
  User,
  LogOut as LogOutIcon,
  ChevronRight,
  History,
} from "lucide-react";

const NAV = [
  { to: "/", icon: LayoutDashboard, label: "Dashboard", end: true },
  { to: "/live", icon: Radio, label: "Live Monitoring" },
  { to: "/vehicles", icon: Car, label: "Vehicle Records" },
  { to: "/entry-history", icon: LogInIcon, label: "Entry History" },
  { to: "/exit-history", icon: LogOut, label: "Exit History" },
  { to: "/reports", icon: FileBarChart, label: "Reports" },
  { to: "/analytics", icon: BarChart3, label: "Analytics" },
  { to: "/settings", icon: SettingsIcon, label: "Settings" },
  { to: "/users", icon: UsersIcon, label: "Users" },
];

export default function Layout({ children }) {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [search, setSearch] = useState("");

  const handleSearch = (e) => {
    e.preventDefault();
    if (search.trim()) {
      navigate(`/vehicles?q=${encodeURIComponent(search.trim())}`);
    }
  };

  return (
    <div className="min-h-screen bg-[#0F172A] text-white relative overflow-hidden">
      {/* Ambient background */}
      <div className="absolute inset-0 grid-bg opacity-40 pointer-events-none" />
      <div className="absolute -top-40 -left-40 w-[520px] h-[520px] rounded-full bg-blue-600/10 blur-[120px] pointer-events-none" />
      <div className="absolute top-1/3 -right-40 w-[520px] h-[520px] rounded-full bg-sky-500/10 blur-[120px] pointer-events-none" />

      <div className="relative flex min-h-screen">
        {/* Sidebar */}
        <aside className="hidden md:flex w-64 flex-col glass border-r border-white/10 sticky top-0 h-screen" data-testid="sidebar">
          <div className="px-6 pt-6 pb-8 flex items-center gap-3">
            <div className="h-10 w-10 rounded-xl bg-gradient-to-br from-blue-500 to-blue-700 flex items-center justify-center shadow-lg shadow-blue-900/40">
              <span className="text-white font-bold tracking-tight">R</span>
            </div>
            <div className="leading-tight">
              <div className="text-sm font-semibold tracking-wide">RDX</div>
              <div className="text-[11px] text-slate-400">Car Showroom</div>
            </div>
          </div>

          <nav className="px-3 flex-1 space-y-1">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                data-testid={`nav-${item.label.toLowerCase().replace(/\s+/g, "-")}`}
                className={({ isActive }) =>
                  `group flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-all duration-200 ${
                    isActive
                      ? "bg-blue-600/15 text-white border border-blue-500/30 shadow-inner"
                      : "text-slate-400 hover:text-white hover:bg-white/5 border border-transparent"
                  }`
                }
              >
                <item.icon className="h-4 w-4 shrink-0" />
                <span className="flex-1">{item.label}</span>
                <ChevronRight className="h-3.5 w-3.5 opacity-0 group-hover:opacity-60 transition-opacity" />
              </NavLink>
            ))}
          </nav>

          <div className="p-4 mt-2">
            <div className="glass-strong rounded-2xl p-4">
              <div className="text-xs text-slate-400 mb-1">System Status</div>
              <div className="flex items-center gap-2">
                <span className="text-emerald-400 pulse-dot text-sm">Operational</span>
              </div>
              <div className="mt-2 text-[11px] text-slate-500">180-day retention active</div>
            </div>
          </div>
        </aside>

        {/* Main */}
        <div className="flex-1 flex flex-col min-w-0">
          {/* Top nav */}
          <header className="glass sticky top-0 z-40 border-b border-white/10" data-testid="topbar">
            <div className="flex items-center gap-3 px-4 md:px-8 h-16">
              <div className="md:hidden h-9 w-9 rounded-lg bg-gradient-to-br from-blue-500 to-blue-700 flex items-center justify-center">
                <span className="text-white text-sm font-bold">R</span>
              </div>

              <form onSubmit={handleSearch} className="hidden sm:flex items-center flex-1 max-w-xl">
                <div className="relative w-full">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" />
                  <Input
                    data-testid="topbar-search-input"
                    placeholder="Search vehicles, owners, contacts..."
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    className="pl-9 h-10 bg-white/5 border-white/10 text-white placeholder:text-slate-500 focus-visible:ring-blue-500/40"
                  />
                </div>
              </form>

              <div className="ml-auto flex items-center gap-2">
                <Button variant="ghost" size="icon" className="text-slate-300 hover:bg-white/5 relative" data-testid="topbar-notifications">
                  <Bell className="h-5 w-5" />
                  <span className="absolute top-2 right-2 h-2 w-2 rounded-full bg-blue-400" />
                </Button>

                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <button className="flex items-center gap-2 pl-2 pr-3 py-1.5 rounded-xl hover:bg-white/5 transition-colors" data-testid="topbar-profile">
                      <div className="h-8 w-8 rounded-full bg-gradient-to-br from-blue-500 to-sky-500 flex items-center justify-center text-white text-sm font-semibold">
                        {(user?.name || "A")[0]}
                      </div>
                      <div className="hidden md:block text-left leading-tight">
                        <div className="text-xs font-medium">{user?.name || "Admin"}</div>
                        <div className="text-[10px] text-slate-400 uppercase tracking-wider">{user?.role || "admin"}</div>
                      </div>
                    </button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-56 bg-[#0F172A]/95 border-white/10 text-white">
                    <DropdownMenuLabel className="text-slate-400 text-xs font-normal">{user?.email}</DropdownMenuLabel>
                    <DropdownMenuSeparator className="bg-white/10" />
                    <DropdownMenuItem onSelect={() => navigate("/settings")} className="focus:bg-white/5">
                      <User className="mr-2 h-4 w-4" />Profile
                    </DropdownMenuItem>
                    <DropdownMenuItem onSelect={logout} className="focus:bg-white/5" data-testid="menu-logout">
                      <LogOutIcon className="mr-2 h-4 w-4" />Logout
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            </div>
          </header>

          {/* Mobile nav */}
          <div className="md:hidden overflow-x-auto border-b border-white/10 bg-[#0F172A]/60 backdrop-blur-xl">
            <div className="flex px-3 py-2 gap-1">
              {NAV.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    `flex items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-1.5 text-xs ${
                      isActive ? "bg-blue-600/20 text-white" : "text-slate-400 hover:text-white"
                    }`
                  }
                >
                  <item.icon className="h-3.5 w-3.5" />
                  {item.label}
                </NavLink>
              ))}
            </div>
          </div>

          <main className="flex-1 px-4 md:px-8 py-6 md:py-8">
            <AnimatePresence mode="wait">
              <motion.div
                key={location.pathname}
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                transition={{ duration: 0.35 }}
              >
                {children}
              </motion.div>
            </AnimatePresence>
          </main>

          <footer className="border-t border-white/10 px-4 md:px-8 py-5 text-center text-xs text-slate-500">
            © 2026 hyundai Car Showroom Management System
            <span className="hidden md:inline"> • <span className="text-slate-400">RDX</span> Premium Enterprise</span>
          </footer>
        </div>
      </div>
    </div>
  );
}
