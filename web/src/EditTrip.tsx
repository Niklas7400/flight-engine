import { useState } from "react";
import type { FormEvent } from "react";
import { supabase } from "./supabase";
import type { Trip } from "./types";
import { TripFields, useProfileForm, validateProfile } from "./TripForm";

export default function EditTrip({ trip, onSaved, onDeleted }: { trip: Trip; onSaved: () => Promise<void> | void; onDeleted: () => Promise<void> | void }) {
  const f = useProfileForm(trip.profile);
  const { msg, setMsg } = f;
  const [busy, setBusy] = useState(false);

  // Ungespeicherte Aenderungen: aktueller Stand gegen den zuletzt gespeicherten
  const dirty = JSON.stringify(f.current()) !== JSON.stringify({
    ...trip.profile,
    origins: [...new Set(trip.profile.origins)],
    destinations: [...new Set(trip.profile.destinations)],
  });

  async function save(e: FormEvent) {
    e.preventDefault();
    const profile = f.current();
    const err = validateProfile(profile);
    if (err) return setMsg({ ok: false, text: err });
    setBusy(true);
    const { error } = await supabase.from("trips").update({ profile }).eq("id", trip.id);
    setBusy(false);
    if (error) return setMsg({ ok: false, text: error.message });
    setMsg({ ok: true, text: "Gespeichert. Für neue Daten oder Flughäfen in der Übersicht „Volle Suche“ starten." });
    await onSaved();
  }

  async function remove() {
    if (!window.confirm(`Trip ‚${trip.name}‘ und alle gespeicherten Preise endgültig löschen?`)) return;
    setBusy(true);
    const { error } = await supabase.from("trips").delete().eq("id", trip.id);
    if (error) {
      setBusy(false);
      return setMsg({ ok: false, text: error.message });
    }
    await onDeleted();
  }

  return (
    <div className="stack">
      <form className="card stack" onSubmit={save}>
        <h2>Trip bearbeiten</h2>
        <TripFields f={f} />
        <div className="save-row">
          <button type="submit" disabled={busy || !dirty}>Speichern</button>
          <span className="muted">{busy ? "Speichert …" : dirty ? "Ungespeicherte Änderungen" : msg?.ok ? "" : "Alles gespeichert"}</span>
        </div>
        {msg && <p className={msg.ok ? "ok" : "error"}>{msg.text}</p>}
      </form>
      <section className="card stack">
        <h2>Trip löschen</h2>
        <p className="muted hint">Löscht den Trip „{trip.name}“ samt allen gespeicherten Preisen und Empfehlungen.</p>
        <button type="button" className="ghost danger" onClick={() => void remove()} disabled={busy}>Trip löschen</button>
      </section>
    </div>
  );
}
