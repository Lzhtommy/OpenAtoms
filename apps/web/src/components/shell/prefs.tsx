"use client";
import { Languages, Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tip } from "@/components/ui/tooltip";
import { useLang } from "@/lib/i18n";
import { useTheme } from "@/lib/theme";

export function ThemeToggle() {
  const { dark, toggle } = useTheme();
  return (
    <Tip label={dark ? "Light" : "Dark"}>
      <Button variant="ghost" size="icon-sm" onClick={toggle} aria-label="theme">
        {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
      </Button>
    </Tip>
  );
}
export function LangToggle() {
  const { lang, setLang } = useLang();
  return (
    <Tip label={lang === "zh" ? "English" : "中文"}>
      <Button variant="ghost" size="icon-sm" onClick={() => setLang(lang === "zh" ? "en" : "zh")} aria-label="language">
        <Languages className="h-4 w-4" />
      </Button>
    </Tip>
  );
}
