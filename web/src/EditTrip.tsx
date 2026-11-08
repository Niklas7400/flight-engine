import { useState } from "react";
import type { FormEvent } from "react";
import { supabase } from "./supabase";
import type { Preferences, Profile, Trip } from "./types";

const IATA = /^[A-Z]{3}$/;

const PREFS: [keyof Preferences, string][] = [
  ["avoid_early_departure", "Früher Abflug (vor 7 Uhr)"],
  ["avoid_late_arrival", "Späte Ankunft (nach 23 Uhr)"],
  ["avoid_stops", "Umstieg"],
  ["avoid_long_duration", "Deutlich längere Reisezeit als die schnellste Verbindung"],
  ["avoid_length_deviation", "Abweichung von der Wunschdauer"],
];

function pad(list: string[]): string[] {
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

export default function EditTrip({ trip, onSaved }: { trip: Trip; onSaved: () => Promise<void> | void }) {
  const [p, setP] = useState<Profile>(trip.profile);
  const [origins, setOrigins] = useState(pad(trip.profile.origins));
  const [dests, setDests] = useState(pad(trip.profile.destinations));
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const set = <K extends keyof Profile>(k: K, v: Profile[K]) => setP((cur) => ({ ...cur, [k]: v }));
  const num = (v: string) => (v === "" ? 0 : Number(v));

  function validate(o: string[], d: string[]): string | null {
    if (o.length < 1) return "Mindestens ein Abflughafen angeben.";
    if (d.length < 1) return "Mindestens ein Zielflughafen angeben.";
    if (![...o, ...d].every((c) => IATA.test(c))) return "Flughafen-Codes müssen aus 3 Großbuchstaben bestehen.";
    if (!p.earliest_departure || !p.latest_return) return "Zeitraum angeben.";
    if (p.earliest_departure >= p.latest_return) return "Früheste Abreise muss vor dem spätesten Rückflug liegen.";
    if (!(p.min_nights <= p.nights && p.nights <= p.max_nights)) return "Es muss gelten: Minimum ≤ Wunschdauer ≤ Maximum.";
    if (p.flex_days < 0 || p.flex_days > 3) return "Flexible Tage: 0 bis 3.";
    return null;
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    const o = [...new Set(origins.filter(Boolean))];
    const d = [...new Set(dests.filter(Boolean))];
    const err = validate(o, d);
    if (err) return setMsg({ ok: false, text: err });
    setBusy(true);
    const profile: Profile = { ...p, origins: o, destinations: d };
    const { error } = await supabase.from("trips").update({ profile }).eq("id", trip.id);
    setBusy(false);
    if (error) return setMsg({ ok: false, text: error.message });
    setMsg({ ok: true, text: "Gespeichert. Für neue Daten oder Flughäfen in der Übersicht „Volle Suche“ starten." });
    await onSaved();
  }

  return (
    <form className="card stack" onSubmit={save}>
      <h2>Trip bearbeiten</h2>
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
        {PREFS.map(([k, label]) => (
          <div className="pref" key={k}>
            <span>{label}</span>
            <button
              type="button"
              role="switch"
              aria-checked={p.preferences[k]}
              className={p.preferences[k] ? "toggle on" : "toggle"}
              onClick={() => set("preferences", { ...p.preferences, [k]: !p.preferences[k] })}
            >
              {p.preferences[k] ? "stört mich" : "egal"}
            </button>
          </div>
        ))}
      </fieldset>
      <button type="submit" disabled={busy}>Speichern</button>
      <p className="muted">Wirkt beim nächsten Suchlauf.</p>
      {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text}</p>}
    </form>
  );
}
