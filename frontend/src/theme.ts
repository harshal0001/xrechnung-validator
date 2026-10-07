/** Light, dark, or whatever the operating system says.
 *
 * The choice is applied as `data-theme` on <html>, and the stylesheet does the
 * rest with `color-scheme` and `light-dark()`. "system" removes the attribute,
 * so the page follows `prefers-color-scheme` and keeps following it if the
 * system switches at dusk.
 *
 * The same read happens in an inline script in index.html before the first
 * paint, so a reader who chose dark never sees a white flash on load. This
 * module keeps the two in step afterwards.
 */

export type Theme = "system" | "light" | "dark";
export const THEMES: readonly Theme[] = ["system", "light", "dark"];

const KEY = "xrv-theme";

export function storedTheme(): Theme {
  try {
    const value = window.localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    // Private windows and blocked storage throw. The page still works; it just
    // forgets the choice.
    return "system";
  }
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
  try {
    if (theme === "system") window.localStorage.removeItem(KEY);
    else window.localStorage.setItem(KEY, theme);
  } catch {
    /* see storedTheme */
  }
}
