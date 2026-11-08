import { useEffect, useMemo, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { supabase } from "./supabase";
import type { Observation, Recommendation, Trip } from "./types";
import { dateTime, eur } from "./format";

const PAGE = 1000;

export default function History({ trip }: { trip: Trip }) {
  const [obs, setObs] = useState<Observation[]>([]);
  const [bestKey, setBestKey] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancel = false;
    (async () => {
      const all: Observation[] = [];
      for (let from = 0; ; from += PAGE) {
        const { data, error: err } = await supabase
          .from("price_observations")
          .select("option_key,observed_at,price")
          .eq("trip_id", trip.id)
          .order("observed_at", { ascending: true })
          .range(from, from + PAGE - 1);
        if (err) { if (!cancel) { setError(err.message); setLoading(false); } return; }
        const rows = (data ?? []) as Observation[];
        all.push(...rows.map((r) => ({ ...r, price: Number(r.price) })));
        if (rows.length < PAGE) break;
      }
      const { data: rec } = await supabase
        .from("recommendations").select("payload").eq("trip_id", trip.id)
        .order("created_at", { ascending: false }).limit(1);
      if (cancel) return;
      setObs(all);
      const p = rec?.[0]?.payload as Recommendation | undefined;
      setBestKey(p?.best?.key ?? null);
      setLoading(false);
    })();
    return () => { cancel = true; };
  }, [trip.id]);

  const daily = useMemo(() => {
    const m = new Map<string, number>();
    for (const o of obs) {
      const d = o.observed_at.slice(0, 10);
      const cur = m.get(d);
      if (cur === undefined || o.price < cur) m.set(d, o.price);
    }
    return [...m.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([day, min]) => ({ day, min }));
  }, [obs]);

  const last = useMemo(
    () => (bestKey ? obs.filter((o) => o.option_key === bestKey).slice(-20).reverse() : []),
    [obs, bestKey],
  );

  if (loading) return <p className="muted">Lädt …</p>;
  if (error) return <p className="error">{error}</p>;
  if (obs.length === 0) return <p className="muted">Noch keine Preisbeobachtungen.</p>;

  return (
    <div className="stack">
      <section className="card">
        <h2>Tagesminimum</h2>
        <div className="chart">
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={daily} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
              <XAxis dataKey="day" tick={{ fill: "var(--muted)", fontSize: 11 }} tickFormatter={(d: string) => d.slice(5)} />
              <YAxis tick={{ fill: "var(--muted)", fontSize: 11 }} width={48} domain={["auto", "auto"]} tickFormatter={(v: number) => `${v}`} />
              <Tooltip
                formatter={(v) => [eur(Number(v)), "Minimum"]}
                contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", color: "var(--fg)" }}
              />
              <Line type="monotone" dataKey="min" stroke="var(--accent)" strokeWidth={2} dot={daily.length < 40} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </section>
      <section className="card">
        <h2>Letzte Beobachtungen (bester Flug)</h2>
        {last.length === 0 ? (
          <p className="muted">Keine Beobachtungen für den aktuellen besten Flug.</p>
        ) : (
          <table>
            <thead><tr><th>Zeitpunkt</th><th className="num">Preis</th></tr></thead>
            <tbody>
              {last.map((o, i) => (
                <tr key={i}><td>{dateTime(o.observed_at)}</td><td className="num">{eur(o.price)}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
