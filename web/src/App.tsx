import { useCallback, useEffect, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { configured, supabase } from "./supabase";
import type { Trip } from "./types";
import Login from "./Login";
import Overview from "./Overview";
import History from "./History";
import EditTrip from "./EditTrip";
import NewTrip from "./NewTrip";

type Tab = "overview" | "history" | "edit";

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  const [trips, setTrips] = useState<Trip[]>([]);
  const [tripName, setTripName] = useState<string>("");
  const [tab, setTab] = useState<Tab>("overview");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setReady(true);
    });
    const { data } = supabase.auth.onAuthStateChange((_e, s) => setSession(s));
    return () => data.subscription.unsubscribe();
  }, []);

  const loadTrips = useCallback(async (select?: string) => {
    setLoading(true);
    const { data, error: err } = await supabase.from("trips").select("id,name,profile,tracking_enabled").order("name");
    setLoading(false);
    if (err) {
      setError(err.message);
      return;
    }
    setError(null);
    const list = (data ?? []) as Trip[];
    setTrips(list);
    setTripName((cur) => (select && list.some((t) => t.name === select) ? select : list.some((t) => t.name === cur) ? cur : (list[0]?.name ?? "")));
  }, []);

  useEffect(() => {
    if (session) void loadTrips();
  }, [session, loadTrips]);

  if (!configured) {
    return (
      <main className="container">
        <p className="error">VITE_SUPABASE_URL und VITE_SUPABASE_PUBLISHABLE_KEY sind nicht gesetzt.</p>
      </main>
    );
  }
  if (!ready) return <main className="container"><p className="muted">Lädt …</p></main>;
  if (!session) return <Login />;

  const trip = trips.find((t) => t.name === tripName);

  return (
    <div className="container">
      <header className="topbar">
        <h1>Flight Engine</h1>
        <button className="ghost" onClick={() => void supabase.auth.signOut()}>Abmelden</button>
      </header>

      <div className="picker">
        {trips.length > 0 && (
          <label className="field">
            Reise
            <select value={tripName} onChange={(e) => { setTripName(e.target.value); setNotice(null); setCreating(false); }}>
              {trips.map((t) => (
                <option key={t.id} value={t.name}>{t.name}{t.tracking_enabled ? "" : " (Tracking aus)"}</option>
              ))}
            </select>
          </label>
        )}
        <button type="button" className="ghost" onClick={() => { setCreating(true); setNotice(null); }} disabled={creating}>+ Neuer Trip</button>
      </div>
      {notice && <p className="ok">{notice}</p>}

      <nav className="tabs" role="tablist">
        {([["overview", "Übersicht"], ["history", "Preisverlauf"], ["edit", "Trip bearbeiten"]] as const).map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "tab active" : "tab"} onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </nav>

      {error && <p className="error">{error}</p>}
      {loading && <p className="muted">Lädt …</p>}
      {!loading && !trip && !error && <p className="muted">Keine Reisen gefunden (oder kein Zugriff).</p>}

      {creating && (
        <NewTrip
          key={trip?.id ?? "none"}
          base={trip}
          existing={trips.map((t) => t.name)}
          onCancel={() => setCreating(false)}
          onCreated={async (slug) => {
            await loadTrips(slug);
            setCreating(false);
            setTab("overview");
            setNotice("Trip angelegt. Starte in der Übersicht „Volle Suche“, um erste Preise zu holen.");
          }}
        />
      )}
      {!creating && trip && tab === "overview" && <Overview key={trip.id} trip={trip} onChanged={() => loadTrips()} />}
      {!creating && trip && tab === "history" && <History key={trip.id} trip={trip} />}
      {!creating && trip && tab === "edit" && <EditTrip key={trip.id} trip={trip} onSaved={() => loadTrips()} onDeleted={async () => { setTripName(""); await loadTrips(); }} />}
    </div>
  );
}
