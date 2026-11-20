import React, { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTheme } from "next-themes";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import {
  Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandSeparator,
} from "@/components/ui/command";
import { Car, Search, SunMoon, Languages, ScanEye, Loader2 } from "lucide-react";
import api from "@/lib/api";
import { navForRole } from "@/lib/nav";
import { useT } from "@/lib/i18n";
import { useAuth } from "@/context/AuthContext";

/** Ctrl+K / ⌘K: jump to a vehicle by plate, owner or phone, open any page, or flip theme / language. */
export default function CommandPalette({ open, onOpenChange }) {
  const navigate = useNavigate();
  const { user } = useAuth();
  const { t, lang, setLang } = useT();
  const { resolvedTheme, setTheme } = useTheme();
  const [q, setQ] = useState("");
  const [vehicles, setVehicles] = useState([]);
  const [loading, setLoading] = useState(false);
  const seq = useRef(0);

  // Global shortcut
  useEffect(() => {
    const onKey = (e) => {
      if ((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        onOpenChange(!open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

  useEffect(() => { if (!open) { setQ(""); setVehicles([]); } }, [open]);

  // Debounced server search for vehicles
  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) { setVehicles([]); setLoading(false); return undefined; }
    const id = ++seq.current;
    setLoading(true);
    const timer = setTimeout(async () => {
      try {
        const { data } = await api.get("/vehicles/search", { params: { q: term, limit: 8 } });
        if (id === seq.current) setVehicles(Array.isArray(data) ? data : []);
      } catch {
        if (id === seq.current) setVehicles([]);
      } finally {
        if (id === seq.current) setLoading(false);
      }
    }, 200);
    return () => clearTimeout(timer);
  }, [q]);

  const go = (to) => { onOpenChange(false); navigate(to); };
  const pages = navForRole(user?.role).flatMap((g) => g.items);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="overflow-hidden p-0 sm:max-w-xl" data-testid="command-palette">
        <DialogTitle className="sr-only">{t("top.commandHint")}</DialogTitle>
        <Command
          className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-muted-foreground [&_[cmdk-group]]:px-2 [&_[cmdk-input]]:h-12 [&_[cmdk-item]]:px-2 [&_[cmdk-item]]:py-2.5"
        >
          <CommandInput value={q} onValueChange={setQ} placeholder={t("cmd.placeholder")} data-testid="command-input" />
          <CommandList className="max-h-[420px]">
            <CommandEmpty>{loading ? <Loader2 className="mx-auto h-4 w-4 animate-spin" /> : t("cmd.empty")}</CommandEmpty>

            {vehicles.length > 0 && (
              <CommandGroup heading={t("cmd.vehicles")}>
                {vehicles.map((v) => (
                  <CommandItem
                    key={v.vehicle_number}
                    value={`${v.vehicle_number} ${v.owner_name || ""} ${v.phone_number || ""} ${v.customer_id || ""}`}
                    onSelect={() => go(`/vehicle/${encodeURIComponent(v.vehicle_number)}`)}
                    data-testid={`command-vehicle-${v.vehicle_number}`}
                  >
                    <Car className="mr-2 h-4 w-4 text-muted-foreground" />
                    <span className="plate mr-2">{v.vehicle_number}</span>
                    <span className="truncate text-sm">{v.owner_name || "—"}</span>
                    {v.active_visits > 0 && (
                      <span className="ml-auto rounded-full bg-success/10 px-2 py-0.5 text-[10px] font-semibold text-success">
                        {t("rv.inside")}
                      </span>
                    )}
                  </CommandItem>
                ))}
              </CommandGroup>
            )}
            {q.trim() && (
              <CommandGroup heading={t("cmd.actions")}>
                <CommandItem value={`search ${q}`} onSelect={() => go(`/search?q=${encodeURIComponent(q.trim())}`)}>
                  <Search className="mr-2 h-4 w-4 text-muted-foreground" /> {t("cmd.searchAll", { q: q.trim() })}
                </CommandItem>
              </CommandGroup>
            )}

            <CommandGroup heading={t("cmd.pages")}>
              {pages.map((p) => (
                <CommandItem key={p.to} value={`${t(p.key)} ${p.label}`} onSelect={() => go(p.to)}>
                  <p.icon className="mr-2 h-4 w-4 text-muted-foreground" /> {t(p.key)}
                </CommandItem>
              ))}
              <CommandItem value={`gate view ${t("cmd.gateView")}`} onSelect={() => go("/reviews?view=gate")}>
                <ScanEye className="mr-2 h-4 w-4 text-muted-foreground" /> {t("cmd.gateView")}
              </CommandItem>
            </CommandGroup>
            <CommandSeparator />
            <CommandGroup heading={t("cmd.actions")}>
              <CommandItem value="theme dark light" onSelect={() => { setTheme(resolvedTheme === "dark" ? "light" : "dark"); onOpenChange(false); }}>
                <SunMoon className="mr-2 h-4 w-4 text-muted-foreground" /> {t("cmd.toggleTheme")}
              </CommandItem>
              <CommandItem value="language hindi english भाषा" onSelect={() => { setLang(lang === "hi" ? "en" : "hi"); onOpenChange(false); }}>
                <Languages className="mr-2 h-4 w-4 text-muted-foreground" /> {t("cmd.toggleLanguage")}
              </CommandItem>
            </CommandGroup>
          </CommandList>
        </Command>
      </DialogContent>
    </Dialog>
  );
}
