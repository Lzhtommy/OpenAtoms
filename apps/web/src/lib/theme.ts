"use client";
import { create } from "zustand";

interface ThemeState {
  dark: boolean;
  toggle: () => void;
}
export const useTheme = create<ThemeState>((set, get) => ({
  dark: false,
  toggle: () => {
    const dark = !get().dark;
    applyTheme(dark);
    set({ dark });
  },
}));
export function applyTheme(dark: boolean) {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle("dark", dark);
  document.documentElement.classList.toggle("light", !dark);
  document.cookie = `app-theme=${dark ? "dark" : "light"};path=/;max-age=31536000`;
  localStorage.setItem("oa_theme", dark ? "dark" : "light");
}
export function initTheme() {
  if (typeof window === "undefined") return;
  const dark = localStorage.getItem("oa_theme") === "dark";
  applyTheme(dark);
  useTheme.setState({ dark });
}
