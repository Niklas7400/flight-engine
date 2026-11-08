import type { Action, Leg } from "./types";

export const eur = (n: number | null | undefined): string =>
  n == null ? "–" : new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(n);

export const duration = (min: number): string => `${Math.floor(min / 60)}:${String(min % 60).padStart(2, "0")} h`;

export const timeOf = (iso: string): string => iso.slice(11, 16);

export const dateOf = (iso: string): string => {
  const d = new Date(iso.slice(0, 10) + "T12:00:00");
  return d.toLocaleDateString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit", year: "numeric" });
};

export const dateTime = (iso: string): string =>
  new Date(iso).toLocaleString("de-DE", { dateStyle: "medium", timeStyle: "short" });

export const actionLabel = (a: Action): string =>
  a === "BUY_NOW" ? "Jetzt buchen" : a === "WAIT" ? "Warten" : "Weiter beobachten";

export const confidenceLabel = (c: string): string =>
  ({ low: "gering", medium: "mittel", high: "hoch" } as Record<string, string>)[c] ?? c;

export const stopsLabel = (n: number): string => (n === 0 ? "direkt" : n === 1 ? "1 Umstieg" : `${n} Umstiege`);

export const routeOf = (l: Leg): string => l.route.join(" → ");

export function googleFlightsUrl(origin: string, dest: string, out: string, ret: string): string {
  const q = `Flights from ${origin} to ${dest} on ${out} through ${ret}`;
  return `https://www.google.com/travel/flights?q=${encodeURIComponent(q)}`;
}
