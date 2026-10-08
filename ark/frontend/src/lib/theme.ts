import { useEffect, useState } from "react";

export type Theme = "dark" | "light" | "rednight";

const KEY = "ark-theme";

export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme);
}

export function getTheme(): Theme {
  const saved = window.localStorage.getItem(KEY) as Theme | null;
  if (saved === "dark" || saved === "light" || saved === "rednight") return saved;
  return "dark";
}

export function setTheme(theme: Theme): void {
  window.localStorage.setItem(KEY, theme);
  applyTheme(theme);
}

export function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setThemeState] = useState<Theme>(getTheme);
  useEffect(() => applyTheme(theme), [theme]);
  return [
    theme,
    (t: Theme) => {
      setTheme(t);
      setThemeState(t);
    },
  ];
}

export const THEMES: { value: Theme; label: string; hint: string }[] = [
  { value: "dark", label: "Dark", hint: "default night-friendly UI" },
  { value: "light", label: "Light", hint: "daylight / print" },
  { value: "rednight", label: "Red night", hint: "low light, preserves night vision" },
];