import React, { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { Moon, Sun, Monitor, Languages, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { LANGUAGES, useT } from "@/lib/i18n";

const THEMES = [
  { value: "light", icon: Sun, key: "top.theme.light" },
  { value: "dark", icon: Moon, key: "top.theme.dark" },
  { value: "system", icon: Monitor, key: "top.theme.system" },
];

/** Top-bar menu for light / dark / system theme and English / Hindi. */
export default function ThemeToggle() {
  const { theme, resolvedTheme, setTheme } = useTheme();
  const { lang, setLang, t } = useT();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const Icon = mounted && resolvedTheme === "dark" ? Moon : Sun;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label={`${t("top.theme")} / ${t("top.language")}`} data-testid="topbar-theme">
          <Icon className="h-5 w-5" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-48">
        <DropdownMenuLabel className="text-xs font-medium text-muted-foreground">{t("top.theme")}</DropdownMenuLabel>
        {THEMES.map((o) => (
          <DropdownMenuItem key={o.value} onSelect={() => setTheme(o.value)} data-testid={`theme-${o.value}`}>
            <o.icon className="mr-2 h-4 w-4" /> {t(o.key)}
            {mounted && theme === o.value && <Check className="ml-auto h-4 w-4 text-primary" />}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuLabel className="text-xs font-medium text-muted-foreground">
          <Languages className="mr-1 inline h-3.5 w-3.5" /> {t("top.language")}
        </DropdownMenuLabel>
        {LANGUAGES.map((l) => (
          <DropdownMenuItem key={l.code} onSelect={() => setLang(l.code)} data-testid={`lang-${l.code}`}>
            {l.label}
            {lang === l.code && <Check className="ml-auto h-4 w-4 text-primary" />}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
