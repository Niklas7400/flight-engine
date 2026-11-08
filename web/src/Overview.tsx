import { useEffect, useState } from "react";
import { supabase } from "./supabase";
import AskAI from "./AskAI";
import type { Leg, Recommendation, TimingAdvice, Trip, TripOption } from "./types";
import { actionLabel, dateOf, dateTime, duration, eur, googleFlightsUrl, routeOf, stopsLabel, timeOf } from "./format";

function LegRow({ label, leg }: { label: string; leg: Leg }) {
  return (
    <div className="leg">
      <div className="leg-head"><strong>{label}</strong> {dateOf(leg.departure)}</div>
      <div>{timeOf(leg.departure)} – {timeOf(leg.arrival)} · {duration(leg.duration_min)}</div>
      <div className="muted">{routeOf(leg)} · {stopsLabel(leg.stops)}</div>
      <div className="muted">{leg.airlines.join(", ")}</div>
    </div>
  );
}

function Advice({ title, advice, extra }: { title: string; advice: TimingAdvice; extra?: string }) {
  return (
    <section className={`card action-${advice.action.toLowerCase()}`}>
      <h2>{title}</h2>
      <p className="big">{actionLabel(advice.action)}</p>
      {extra && <p className="warn">{extra}</p>}
      <dl className="facts">
        {advice.decide_by && (<><dt>Entscheiden bis</dt><dd>{dateOf(advice.decide_by)}</dd></>)}
        {advice.target_price != null && (<><dt>Zielpreis</dt><dd>{eur(advice.target_price)}</dd></>)}
        <dt>Sicherheit</dt><dd>{advice.confidence}</dd>
      </dl>
      <ul>{advice.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
    </section>
  );
}

function Trigger({ name }: { name: string }) {
  const [status, setStatus] = useState<string>("");
  const [busy, setBusy] = useState(false);
  async function run(mode: "track" | "run") {
    if (mode === "run" && !window.confirm("Volle Suche über den ganzen Zeitraum verbraucht ca. 60 der 250 SerpApi-Suchen im Monat. Starten?")) return;
    setBusy(true);
    setStatus("Suchlauf wird gestartet …");
    const { data, error } = await supabase.functions.invoke("trigger-search", { body: { trip: name, mode } });
    setBusy(false);
    if (error) setStatus(`Fehler: ${error.message}`);
    else if (data && (data as { ok?: boolean }).ok === false) setStatus(`Fehler: ${(data as { error?: string }).error ?? "unbekannt"}`);
    else setStatus("Suchlauf gestartet. Ergebnisse erscheinen in einigen Minuten.");
  }
  return (
    <div className="trigger">
      <button onClick={() => void run("track")} disabled={busy}>Preise aktualisieren (~10 Suchen)</button>
      <button className="ghost" onClick={() => void run("run")} disabled={busy}>Volle Suche (~60 Suchen, nach Änderungen am Trip)</button>
      {status && <p className="muted">{status}</p>}
    </div>
  );
}

function OptionCompact({ o }: { o: TripOption }) {
  return (
    <li className="alt">
      <div>
        <strong>{eur(o.price)}</strong> · {o.nights} Nächte
        <div className="muted">
          {dateOf(o.outbound.departure)} – {dateOf(o.inbound.departure)}
        </div>
        <div className="muted">
          {o.outbound.route.join("→")} · {stopsLabel(Math.max(o.outbound.stops, o.inbound.stops))} · {o.outbound.airlines.join(", ")}
        </div>
      </div>
    </li>
  );
}

export default function Overview({ trip }: { trip: Trip }) {
  const [rec, setRec] = useState<Recommendation | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancel = false;
    (async () => {
      const { data, error: err } = await supabase
        .from("recommendations")
        .select("created_at,payload")
        .eq("trip_id", trip.id)
        .order("created_at", { ascending: false })
        .limit(1);
      if (cancel) return;
      setLoading(false);
      if (err) setError(err.message);
      else if (data && data.length > 0) {
        const row = data[0] as { created_at: string; payload: Recommendation };
        setRec({ ...row.payload, created_at: row.payload.created_at ?? row.created_at });
      }
    })();
    return () => { cancel = true; };
  }, [trip.id]);

  if (loading) return <p className="muted">Lädt …</p>;

  return (
    <div className="stack">
      <Trigger name={trip.name} />
      {error && <p className="error">{error}</p>}
      {!rec && !error && <p className="muted">Noch keine Empfehlung vorhanden. Starte einen Suchlauf.</p>}
      {rec && (() => {
        const best = rec.best;
        const diverge = rec.timing_llm && rec.timing_llm.action !== rec.timing_rules.action;
        const diff = best.price - rec.cheapest.price;
        const url = googleFlightsUrl(best.outbound.origin, best.outbound.destination, best.outbound.departure.slice(0, 10), best.inbound.departure.slice(0, 10));
        return (
          <>
            <Advice title="Empfehlung (Regeln)" advice={rec.timing_rules} />
            {rec.timing_llm && (
              <Advice title="Einschätzung KI" advice={rec.timing_llm} extra={diverge ? "Regeln und KI sind sich uneinig" : undefined} />
            )}
            <section className="card">
              <h2>Bester Flug</h2>
              <p className="big">{eur(best.price)} <span className="muted">· {best.nights} Nächte</span></p>
              <LegRow label="Hin" leg={best.outbound} />
              <LegRow label="Rück" leg={best.inbound} />
              <p>{rec.explanation}</p>
              <a href={url} target="_blank" rel="noopener noreferrer">Bei Google Flights suchen</a>
            </section>
            {rec.cheapest.key !== best.key && (
              <section className="card small">
                <h3>Günstigste Option</h3>
                <p>
                  {eur(rec.cheapest.price)} ({diff > 0 ? `${eur(diff)} günstiger` : `${eur(-diff)} teurer`}) · {rec.cheapest.nights} Nächte
                </p>
                <p className="muted">
                  {dateOf(rec.cheapest.outbound.departure)} – {dateOf(rec.cheapest.inbound.departure)} · {rec.cheapest.outbound.route.join("→")} · {stopsLabel(Math.max(rec.cheapest.outbound.stops, rec.cheapest.inbound.stops))}
                </p>
              </section>
            )}
            {rec.hints && rec.hints.length > 0 && (
              <section className="card small">
                <h3>Direktflüge ohne Preis bei Google</h3>
                <p className="muted">Nicht in der Bewertung enthalten, können aber günstig sein. Preis direkt bei der Airline prüfen.</p>
                <ul>{rec.hints.map((h) => <li key={h}>{h}</li>)}</ul>
              </section>
            )}
            {rec.alternatives.length > 0 && (
              <section className="card">
                <h2>Alternativen</h2>
                <ul className="alts">{rec.alternatives.map((o) => <OptionCompact key={o.key} o={o} />)}</ul>
              </section>
            )}
            <AskAI profile={trip.profile} rec={rec} />
            <footer className="muted foot">
              Stand: {dateTime(rec.created_at)}
              {rec.api_calls_remaining_month != null && <> · Verbleibende API-Calls im Monat: {rec.api_calls_remaining_month}</>}
            </footer>
          </>
        );
      })()}
    </div>
  );
}
