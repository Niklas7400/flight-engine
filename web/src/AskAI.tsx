import { useMemo, useState } from "react";
import type { Leg, Profile, Recommendation, TripOption } from "./types";
import { actionLabel, dateOf, duration, eur, timeOf } from "./format";

const PREF_TEXT: [keyof Profile["preferences"], string][] = [
  ["avoid_early_departure", "früher Abflug (vor 7 Uhr)"],
  ["avoid_late_arrival", "späte Ankunft (nach 23 Uhr)"],
  ["avoid_stops", "Umstieg"],
  ["avoid_long_duration", "deutlich längere Reisezeit als die schnellste Verbindung"],
  ["avoid_length_deviation", "Abweichung von der Wunschdauer"],
];

function legText(leg: Leg): string {
  const stops = leg.stops === 0 ? "direkt"
    : `${leg.stops} ${leg.stops === 1 ? "Umstieg" : "Umstiege"} über ${leg.route.slice(1, -1).join(", ")}`;
  return `${dateOf(leg.departure)} ${timeOf(leg.departure)}–${timeOf(leg.arrival)} ${leg.route[0]}→${leg.route[leg.route.length - 1]}, ` +
    `${leg.airlines.join("/")}, ${stops}, ${duration(leg.duration_min)}`;
}

function optionText(o: TripOption): string {
  const tickets = o.source === "oneway_combo" ? "zwei getrennte One-Way-Tickets" : "ein Roundtrip-Ticket";
  return `${eur(o.price)} (${o.nights} Nächte, ${tickets})\n  Hin: ${legText(o.outbound)}\n  Rück: ${legText(o.inbound)}`;
}

/** Baut einen eigenständigen Prompt aus Trip-Profil und letzter Empfehlung. */
export function buildPrompt(profile: Profile, rec: Recommendation): string {
  const bothers = PREF_TEXT.filter(([k]) => profile.preferences[k]).map(([, t]) => t);
  const fine = PREF_TEXT.filter(([k]) => !profile.preferences[k]).map(([, t]) => t);
  const alts = rec.alternatives.slice(0, 4).map((o) => `- ${optionText(o)}`).join("\n");
  const lines = [
    "Hilf mir bitte, den besten Flug und den richtigen Buchungszeitpunkt zu finden. " +
      "Recherchiere aktuelle Informationen im Web und nenne deine Quellen. Antworte auf Deutsch und knapp.",
    "",
    "## Meine Reise",
    `- Von: ${profile.origins.join(", ")} nach: ${profile.destinations.join(", ")}`,
    `- Zeitraum: frühestens ab ${dateOf(profile.earliest_departure)}, spätestens zurück ${dateOf(profile.latest_return)} ` +
      `(${profile.flex_days} ${profile.flex_days === 1 ? "Tag" : "Tage"} Spielraum an den Rändern)`,
    `- Dauer: Wunsch ${profile.nights} Nächte, ${profile.min_nights}–${profile.max_nights} möglich`,
    "- 1 Person, günstigster Tarif, Gepäck egal",
    `- Stört mich: ${bothers.length ? bothers.join("; ") : "nichts"}`,
    `- Egal: ${fine.length ? fine.join("; ") : "nichts"}`,
    "",
    `## Ergebnis meines Preis-Trackers (Google Flights via SerpApi, Stand ${dateOf(rec.created_at)})`,
    `Beste Option nach meinen Vorlieben:\n- ${optionText(rec.best)}`,
  ];
  if (rec.cheapest.key !== rec.best.key) lines.push(`Günstigste Option:\n- ${optionText(rec.cheapest)}`);
  if (alts) lines.push(`Weitere Optionen:\n${alts}`);
  if (rec.hints?.length) lines.push("Ohne Preis bei Google:", ...rec.hints.map((h) => `- ${h}`));
  lines.push(
    "",
    "## Einschätzung meines Tools",
    `Regeln: ${actionLabel(rec.timing_rules.action)}` +
      (rec.timing_rules.decide_by ? `, spätestens bis ${dateOf(rec.timing_rules.decide_by)}` : ""),
    ...rec.timing_rules.reasons.map((r) => `- ${r}`),
  );
  if (rec.timing_llm) {
    lines.push(`Zweite KI-Einschätzung: ${actionLabel(rec.timing_llm.action)}`,
      ...rec.timing_llm.reasons.map((r) => `- ${r}`));
  }
  // Fragen gelten fuer jede Strecke; die Frage zu Fluegen ohne Preis nur, wenn es welche gibt
  const questions = [
    "Jetzt buchen oder warten? Falls warten: bis wann und ab welchem Preis zuschlagen?",
    ...(rec.hints?.length ? ["Was kosten die oben genannten Flüge ohne Preis bei Google aktuell bei der Airline?"] : []),
    "Gibt es bessere Alternativen, die mein Tool nicht sieht (andere Abflughäfen in der Nähe, andere Airlines, Pauschalangebote)?",
    "Worauf sollte ich bei der Buchung achten (Gepäck, Umsteigezeiten, getrennte Tickets)?",
  ];
  lines.push("", "## Meine Fragen", ...questions.map((q, i) => `${i + 1}. ${q}`));
  return lines.join("\n");
}

async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

export default function AskAI({ profile, rec }: { profile: Profile; rec: Recommendation }) {
  const prompt = useMemo(() => buildPrompt(profile, rec), [profile, rec]);
  const [status, setStatus] = useState("");
  const q = encodeURIComponent(prompt);

  async function onCopy() {
    setStatus((await copy(prompt)) ? "Kopiert. In eine beliebige KI einfügen." : "Kopieren nicht möglich – Text unten markieren und kopieren.");
  }
  async function onGemini() {
    // Gemini nimmt keinen Prompt per Link an: kopieren, dann öffnen
    const ok = await copy(prompt);
    setStatus(ok ? "Kopiert. In Gemini einfügen (Strg+V / lange tippen → Einfügen)." : "Kopieren nicht möglich – Text unten markieren und kopieren.");
    window.open("https://gemini.google.com/app", "_blank", "noopener");
  }

  return (
    <section className="card">
      <h2>Eigene KI fragen</h2>
      <p className="muted">
        Alle Daten zu deiner Reise als fertiger Prompt, z. B. für eine zweite Meinung mit aktueller Websuche.
      </p>
      <div className="ai-buttons">
        <button type="button" onClick={() => void onCopy()}>Prompt kopieren</button>
        <a className="button ghost" href={`https://claude.ai/new?q=${q}`} target="_blank" rel="noopener noreferrer">In Claude öffnen</a>
        <a className="button ghost" href={`https://chatgpt.com/?q=${q}`} target="_blank" rel="noopener noreferrer">In ChatGPT öffnen</a>
        <button type="button" className="ghost" onClick={() => void onGemini()}>In Gemini öffnen</button>
      </div>
      {status && <p className="muted">{status}</p>}
      <details>
        <summary>Prompt ansehen</summary>
        <textarea className="prompt" readOnly value={prompt} rows={14} onFocus={(e) => e.currentTarget.select()} />
      </details>
    </section>
  );
}
