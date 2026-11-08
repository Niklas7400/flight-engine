import { useState } from "react";
import type { Preferences, Profile } from "./types";

export const IATA = /^[A-Z]{3}$/;

const PREFS: [keyof Preferences, string][] = [
  ["avoid_early_departure", "Früher Abflug (vor 7 Uhr)"],
  ["avoid_late_arrival", "Späte Ankunft (nach 23 Uhr)"],
  ["avoid_stops", "Umstieg"],
  ["avoid_long_duration", "Deutlich längere Reisezeit als die schnellste Verbindung"],
  ["avoid_length_deviation", "Abweichung von der Wunschdauer"],
];

export function pad(list: string[]): string[] {
  return [...list, "", "", ""].slice(0, 3);
}

function AirportFields({ label, values, onChange }: { label: string; values: string[]; onChange: (v: string[]) => void }) {
  return (
    <fieldset>
      <legend>{label}</legend>
      <div className="row">
        {values.map((v, i) => {
          const bad = v !== "" && !IATA.test(v);
          return (
            <input
              key={i}
              value={v}
              maxLength={3}
              placeholder="IATA"
              aria-label={`${label} ${i + 1}`}
              aria-invalid={bad}
              className={bad ? "invalid" : ""}
              onChange={(e) => onChange(values.map((x, j) => (j === i ? e.target.value.toUpperCase() : x)))}
            />
          );
        })}
      </div>
    </fieldset>
  );
}

export function useProfileForm(initial: Profile) {
  const [p, setP] = useState<Profile>(initial);
  const [origins, setOrigins] = useState(pad(initial.origins));
  const [dests, setDests] = useState(pad(initial.destinations));
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const set = <K extends keyof Profile>(k: K, v: Profile[K]) => {
    setMsg(null);
    setP((cur) => ({ ...cur, [k]: v }));
  };
  const current = (): Profile => ({
    ...p,
    origins: [...new Set(origins.filter(Boolean))],
    destinations: [...new Set(dests.filter(Boolean))],
  });
  return {
    p, set, origins, dests, msg, setMsg, current,
    setOrigins: (v: string[]) => { setMsg(null); setOrigins(v); },
    setDests: (v: string[]) => { setMsg(null); setDests(v); },
  };
}
export type ProfileForm = ReturnType<typeof useProfileForm>;

export function validateProfile(p: Profile): string | null {
  if (p.origins.length < 1) return "Mindestens ein Abflughafen angeben.";
  if (p.destinations.length < 1) return "Mindestens ein Zielflughafen angeben.";
  if (![...p.origins, ...p.destinations].every((c) => IATA.test(c))) return "Flughafen-Codes müssen aus 3 Großbuchstaben bestehen.";
  if (!p.earliest_departure || !p.latest_return) return "Zeitraum angeben.";
  if (p.earliest_departure >= p.latest_return) return "Früheste Abreise muss vor dem spätesten Rückflug liegen.";
  if (!(p.min_nights <= p.nights && p.nights <= p.max_nights)) return "Es muss gelten: Minimum ≤ Wunschdauer ≤ Maximum.";
  if (p.flex_days < 0 || p.flex_days > 3) return "Flexible Tage: 0 bis 3.";
  return null;
}

const num = (v: string) => (v === "" ? 0 : Number(v));

/** Gemeinsame Formularfelder fuer „Trip bearbeiten“ und „Neuer Trip“. */
export function TripFields({ f }: { f: ProfileForm }) {
  const { p, set, origins, dests, setOrigins, setDests } = f;
  return (
    <>
      <div className="row">
        <label className="field">Früheste Abreise
          <input type="date" required value={p.earliest_departure} onChange={(e) => set("earliest_departure", e.target.value)} />
        </label>
        <label className="field">Spätester Rückflug
          <input type="date" required value={p.latest_return} onChange={(e) => set("latest_return", e.target.value)} />
        </label>
      </div>
      <div className="row">
        <label className="field">Nächte (Wunsch)
          <input type="number" min={1} value={p.nights} onChange={(e) => set("nights", num(e.target.value))} />
        </label>
        <label className="field">Minimum
          <input type="number" min={1} value={p.min_nights} onChange={(e) => set("min_nights", num(e.target.value))} />
        </label>
        <label className="field">Maximum
          <input type="number" min={1} value={p.max_nights} onChange={(e) => set("max_nights", num(e.target.value))} />
        </label>
        <label className="field">Flexible Tage
          <input type="number" min={0} max={3} value={p.flex_days} onChange={(e) => set("flex_days", num(e.target.value))} />
        </label>
      </div>
      <AirportFields label="Abflughäfen (bis zu 3)" values={origins} onChange={setOrigins} />
      <AirportFields label="Zielflughäfen (bis zu 3)" values={dests} onChange={setDests} />
      <fieldset>
        <legend>Was stört dich?</legend>
        <p className="muted hint">Was dich stört, bekommt bei der Bewertung einen Aufschlag. Markiert ist die aktuelle Einstellung.</p>
        {PREFS.map(([k, label]) => (
          <div className="pref" key={k}>
            <span id={`pref-${k}`}>{label}</span>
            <div className="segmented" role="radiogroup" aria-labelledby={`pref-${k}`}>
              {([[true, "stört mich"], [false, "egal"]] as const).map(([value, text]) => (
                <button
                  key={text}
                  type="button"
                  role="radio"
                  aria-checked={p.preferences[k] === value}
                  className={p.preferences[k] === value ? "seg selected" : "seg"}
                  onClick={() => set("preferences", { ...p.preferences, [k]: value })}
                >
                  {p.preferences[k] === value ? "✓ " : ""}{text}
                </button>
              ))}
            </div>
          </div>
        ))}
      </fieldset>
    </>
  );
}
