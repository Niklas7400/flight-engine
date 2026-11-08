import { useState } from "react";
import type { FormEvent } from "react";
import { supabase } from "./supabase";
import type { Trip } from "./types";
import { TripFields, useProfileForm, validateProfile } from "./TripForm";

/** Anzeigename -> Slug (^[a-z0-9_]{1,64}$): Umlaute aufgeloest, Rest als _ */
export function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").replace(/ß/g, "ss")
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 64);
}

export default function NewTrip({ base, existing, onCreated, onCancel }: {
  base: Trip | undefined;
  existing: string[];
  onCreated: (slug: string) => Promise<void> | void;
  onCancel: () => void;
}) {
  const f = useProfileForm(base?.profile ?? {
    name: "", origins: [], destinations: [], earliest_departure: "", latest_return: "",
    nights: 7, min_nights: 5, max_nights: 9, flex_days: 0, allow_open_jaw: false, currency: "EUR",
    preferences: { avoid_early_departure: true, avoid_late_arrival: true, avoid_stops: true, avoid_long_duration: true, avoid_length_deviation: true },
  });
  const { msg, setMsg } = f;
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const slug = slugify(name);
  const clash = slug !== "" && existing.includes(slug);

  async function create(e: FormEvent) {
    e.preventDefault();
    if (!name.trim() || slug === "") return setMsg({ ok: false, text: "Name angeben (mindestens ein Buchstabe oder eine Ziffer)." });
    if (clash) return setMsg({ ok: false, text: `Ein Trip mit dem Namen „${slug}“ existiert bereits.` });
    const profile = { ...f.current(), name: slug };
    const err = validateProfile(profile);
    if (err) return setMsg({ ok: false, text: err });
    setBusy(true);
    const { error } = await supabase.from("trips").insert({ name: slug, profile });
    setBusy(false);
    if (error) return setMsg({ ok: false, text: error.message });
    await onCreated(slug);
  }

  return (
    <form className="card stack" onSubmit={create}>
      <h2>Neuer Trip</h2>
      <label className="field">Name
        <input value={name} required maxLength={80} placeholder="z. B. Lissabon Mai" onChange={(e) => { setMsg(null); setName(e.target.value); }} />
      </label>
      <p className={clash ? "error" : "muted"}>
        {slug ? <>Kennung: <code>{slug}</code>{clash ? " (bereits vergeben)" : ""}</> : "Kennung wird aus dem Namen erzeugt."}
      </p>
      {base && <p className="muted hint">Vorbelegt mit den Werten von „{base.name}“.</p>}
      <TripFields f={f} />
      <div className="save-row">
        <button type="submit" disabled={busy || clash}>Trip anlegen</button>
        <button type="button" className="ghost" onClick={onCancel} disabled={busy}>Abbrechen</button>
      </div>
      {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text}</p>}
    </form>
  );
}
