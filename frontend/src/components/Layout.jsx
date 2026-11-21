import React, { useCallback, useEffect, useState } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  LogOut, Settings as SettingsIcon, Search, Bell, Menu, ChevronDown,
  ShieldCheck, CarFront, CheckCheck, ScanEye, KeyRound,
} from "lucide-react";
import ChangePasswordDialog from "@/components/ChangePasswordDialog";
import CommandPalette from "@/components/CommandPalette";
import ThemeToggle from "@/components/ThemeToggle";
import { navForRole } from "@/lib/nav";
import { useT } from "@/lib/i18n";
import Logo from "@/components/common/Logo";
import StatusBadge from "@/components/common/StatusBadge";
import useLiveEvents from "@/hooks/useLiveEvents";
import { relativeAgo } from "@/lib/time";
import { cn } from "@/lib/utils";
import api from "@/lib/api";

function SidebarNav({ role, onNavigate, badges = {} }) {
  const { t } = useT();
  return (
    <nav className="flex-1 space-y-6 overflow-y-auto px-3 py-2">
      {navForRole(role).map((group) => (
        <div key={group.label}>
          <div className="px-3 pb-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/80">
            {t(group.key)}
          </div>
          <div className="space-y-0.5">
            {group.items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                onClick={onNavigate}
                data-testid={`nav-${item.label.toLowerCase().replace(/\s+/g, "-")}`}
                className={({ isActive }) =>
                  cn(
                    "group relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                    isActive
                      ? "bg-primary/10 text-primary"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground",
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    {isActive && <span className="absolute left-0 top-1.5 bottom-1.5 w-[3px] rounded-r-full bg-primary" />}
                    <item.icon className={cn("h-[18px] w-[18px] shrink-0", isActive ? "text-primary" : "text-muted-foreground/70 group-hover:text-foreground")} />
                    <span className="flex-1 truncate">{t(item.key)}</span>
                    {item.badge && badges[item.badge] > 0 && (
                      <span className="rounded-full bg-warning px-1.5 py-0.5 text-[10px] font-semibold leading-none text-white" data-testid="nav-review-count">
                        {badges[item.badge] > 99 ? "99+" : badges[item.badge]}
                      </span>
                    )}
                  </>
                )}
              </NavLink>
            ))}
          </div>
        </div>
      ))}
    </nav>
  );
}

function SidebarFooter() {
  const { t } = useT();
  return (
    <div className="m-3 rounded-lg border bg-muted/50 p-3">
      <div className="flex items-center gap-2 text-xs font-medium">
        <ShieldCheck className="h-4 w-4 text-success" />
        {t("nav.retention.title")}
      </div>
      <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
        {t("nav.retention.body")}
      </p>
    </div>
  );
}

function Notifications({ onReview }) {
  const { t } = useT();
  const [events, setEvents] = useState([]);
  const [unread, setUnread] = useState(0);
  const connected = useLiveEvents((msg) => {
    if (msg.type === "review.created") {
      onReview?.();
      setEvents((prev) => [{
        id: `rv-${msg.review?.id}`,
        review: true,
        plate: msg.review?.plate || msg.review?.raw || "Unreadable plate",
        owner: msg.review?.reason || "Needs a person to confirm",
        camera: msg.review?.camera_id,
        at: new Date().toISOString(),
      }, ...prev].slice(0, 15));
      setUnread((n) => n + 1);
      return;
    }
    if (msg.type !== "entry.recorded" && msg.type !== "exit.recorded") return;
    const exit = msg.type === "exit.recorded";
    setEvents((prev) => [{
      id: `${msg.session?.id || msg.vehicle_number}-${exit ? "x" : "e"}-${Date.now()}`,
      plate: msg.vehicle_number,
      exit,
      owner: msg.owner?.owner_name || (msg.owner_status === "new" ? t("live.newVisitor") : t("live.unknownOwner")),
      camera: msg.camera_id || (exit ? msg.session?.exit_camera : msg.session?.entry_camera),
      at: new Date().toISOString(),
    }, ...prev].slice(0, 15));
    setUnread((n) => n + 1);
  });

  return (
    <Popover onOpenChange={(o) => o && setUnread(0)}>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" className="relative" data-testid="topbar-notifications" aria-label={t("top.notifications")}>
          <Bell className="h-5 w-5" />
          {unread > 0 && (
            <span className="absolute right-1 top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-semibold text-white">
              {unread > 9 ? "9+" : unread}
            </span>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-0">
        <div className="flex items-center justify-between border-b px-4 py-3">
          <div className="text-sm font-semibold">{t("top.activity")}</div>
          <StatusBadge tone={connected ? "success" : "neutral"} dot={connected}>
            {connected ? t("top.live") : t("top.offline")}
          </StatusBadge>
        </div>
        {events.length === 0 ? (
          <div className="flex flex-col items-center px-4 py-8 text-center">
            <CheckCheck className="h-6 w-6 text-muted-foreground" />
            <div className="mt-2 text-sm font-medium">{t("top.caughtUp")}</div>
            <div className="text-xs text-muted-foreground">{t("top.caughtUpBody")}</div>
          </div>
        ) : (
          <ul className="max-h-80 divide-y overflow-y-auto">
            {events.map((e) => (
              <li key={e.id}>
                <Link to={e.review ? "/reviews" : `/vehicle/${encodeURIComponent(e.plate)}`} className="flex items-start gap-3 px-4 py-3 hover:bg-muted/60">
                  <div className={e.review ? "icon-tile-sm bg-warning/10 text-warning" : e.exit ? "icon-tile-sm bg-info/10 text-info" : "icon-tile-sm bg-success/10 text-success"}>
                    {e.review ? <ScanEye className="h-4 w-4" /> : e.exit ? <LogOut className="h-4 w-4" /> : <CarFront className="h-4 w-4" />}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-sm font-semibold">{e.plate}</span>
                      <span className="text-[11px] text-muted-foreground">{relativeAgo(e.at)}</span>
                    </div>
                    <div className="truncate text-xs text-muted-foreground">
                      {e.review ? `${t("rv.pending")} · ${e.owner}` : `${e.owner} · ${e.exit ? t("live.exited") : t("live.entered")}${e.camera ? ` · ${e.camera}` : ""}`}
                    </div>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </PopoverContent>
    </Popover>
  );
}

export default function Layout({ children }) {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [mobileOpen, setMobileOpen] = useState(false);
  const [pwOpen, setPwOpen] = useState(false);
  const [pendingReviews, setPendingReviews] = useState(0);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const { t } = useT();
  const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || "");

  const refreshReviews = useCallback(async () => {
    try {
      const { data } = await api.get("/plate-reviews/count");
      setPendingReviews(data.pending || 0);
    } catch { /* badge is best-effort */ }
  }, []);

  useEffect(() => {
    refreshReviews();
    const t = setInterval(refreshReviews, 30000);
    window.addEventListener("reviews:changed", refreshReviews);
    return () => { clearInterval(t); window.removeEventListener("reviews:changed", refreshReviews); };
  }, [refreshReviews]);

  useEffect(() => { window.scrollTo(0, 0); }, [location.pathname]);

  const handleSearch = (e) => {
    e.preventDefault();
    const q = search.trim();
    if (q) {
      navigate(`/search?q=${encodeURIComponent(q)}`);
      setSearch("");
    }
  };

  const initials = (user?.name || user?.email || "U").split(/\s+/).map((s) => s[0]).join("").slice(0, 2).toUpperCase();

  return (
    <div className="min-h-screen bg-background">
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col border-r border-sidebar-border bg-sidebar lg:flex" data-testid="sidebar">
        <div className="flex h-16 items-center border-b border-sidebar-border px-5">
          <Link to="/"><Logo /></Link>
        </div>
        <SidebarNav role={user?.role} badges={{ reviews: pendingReviews }} />
        <SidebarFooter />
      </aside>

      {/* Mobile sidebar */}
      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent side="left" className="flex w-72 flex-col p-0">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <div className="flex h-16 items-center border-b px-5"><Logo /></div>
          <SidebarNav role={user?.role} badges={{ reviews: pendingReviews }} onNavigate={() => setMobileOpen(false)} />
          <SidebarFooter />
        </SheetContent>
      </Sheet>

      <div className="flex min-h-screen flex-col lg:pl-64">
        {/* Top bar */}
        <header className="sticky top-0 z-20 border-b bg-card/85 backdrop-blur supports-[backdrop-filter]:bg-card/75" data-testid="topbar">
          <div className="flex h-16 items-center gap-3 px-4 md:px-6">
            <Button variant="ghost" size="icon" className="lg:hidden" onClick={() => setMobileOpen(true)} aria-label={t("top.openMenu")}>
              <Menu className="h-5 w-5" />
            </Button>
            <Link to="/" className="lg:hidden"><Logo showText={false} size="sm" /></Link>

            <form onSubmit={handleSearch} className="hidden flex-1 sm:flex sm:max-w-md">
              <div className="relative w-full">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  data-testid="topbar-search-input"
                  placeholder={t("top.search")}
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="h-10 border-transparent bg-muted pl-9 pr-16 shadow-none focus-visible:bg-card"
                />
                <button
                  type="button"
                  onClick={() => setPaletteOpen(true)}
                  className="absolute right-2 top-1/2 hidden -translate-y-1/2 rounded border bg-card px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground hover:text-foreground md:block"
                  title={t("top.commandHint")}
                  data-testid="topbar-command"
                >
                  {isMac ? "⌘K" : "Ctrl K"}
                </button>
              </div>
            </form>

            <div className="ml-auto flex items-center gap-1">
              <Button variant="ghost" size="icon" className="sm:hidden" onClick={() => setPaletteOpen(true)} aria-label={t("top.commandHint")}>
                <Search className="h-5 w-5" />
              </Button>
              <ThemeToggle />
              <Notifications onReview={refreshReviews} />
              <div className="mx-2 hidden h-6 w-px bg-border sm:block" />
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button className="flex items-center gap-2.5 rounded-lg py-1.5 pl-1.5 pr-2 transition-colors hover:bg-muted" data-testid="topbar-profile">
                    <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
                      {initials}
                    </div>
                    <div className="hidden text-left leading-tight md:block">
                      <div className="text-sm font-medium">{user?.name || "User"}</div>
                      <div className="text-[11px] capitalize text-muted-foreground">{user?.role || "—"}</div>
                    </div>
                    <ChevronDown className="hidden h-4 w-4 text-muted-foreground md:block" />
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-60">
                  <DropdownMenuLabel className="font-normal">
                    <div className="text-sm font-medium">{user?.name}</div>
                    <div className="truncate text-xs text-muted-foreground">{user?.email}</div>
                  </DropdownMenuLabel>
                  <DropdownMenuSeparator />
                  {user?.role === "admin" && (
                    <DropdownMenuItem onSelect={() => navigate("/settings")}>
                      <SettingsIcon className="mr-2 h-4 w-4" /> {t("top.settings")}
                    </DropdownMenuItem>
                  )}
                  <DropdownMenuItem onSelect={() => setPwOpen(true)} data-testid="menu-change-password">
                    <KeyRound className="mr-2 h-4 w-4" /> {t("top.changePassword")}
                  </DropdownMenuItem>
                  <DropdownMenuItem onSelect={logout} className="text-destructive focus:bg-destructive/10 focus:text-destructive" data-testid="menu-logout">
                    <LogOut className="mr-2 h-4 w-4" /> {t("top.signOut")}
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
              <ChangePasswordDialog open={pwOpen} onOpenChange={setPwOpen} />
              <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
            </div>
          </div>
        </header>

        <main className="flex-1 px-4 py-6 md:px-6 lg:px-8 lg:py-8">
          <div key={location.pathname} className="mx-auto w-full max-w-[1440px] animate-fade-in">
            {children}
          </div>
        </main>

        <footer className="border-t px-4 py-4 text-center text-xs text-muted-foreground md:px-8">
          © {new Date().getFullYear()} Vasu Hyundai · Vehicle Management System
        </footer>
      </div>
    </div>
  );
}
