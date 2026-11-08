# Flight Engine

Flugsuche für flexible Reisetermine: Sie durchsucht ein Zeitfenster nach günstigen Hin- und Rückflügen, bewertet die Angebote nach persönlichen Vorlieben und gibt eine Buchungsempfehlung. Die Flugdaten kommen von [SerpApi (Google Flights)](https://serpapi.com/google-flights-api).

> **Status: Prototyp.** Das Projekt ist als Wochenendprojekt entstanden. Die Empfehlungen sind Regeln von Hand und keine validierte Preisprognose, siehe [Status und Grenzen](#status-und-grenzen).

## Was es tut

- **Suche über ein Zeitfenster** statt über einzelne Tage. Beispiel `trips/kapverden_2027.yaml`: Abflug FRA, Ziel Kapverden (SID, BVC, RAI), Reisefenster 19.3.–15.4.2027, Wunschdauer 10 Nächte (erlaubt 8–12). Pro Tag wird ein Abfrage-Call für alle Flughäfen gemacht; passende Hin- und Rückflug-Tage werden kombiniert.
- **Ja/Nein-Vorlieben** in der Trip-YAML: früher Abflug (vor 07:00), späte Ankunft (ab 23:00 oder vor 05:00), Umstieg, deutlich längere Reisezeit, Abweichung von der Wunschdauer. Jeder ausgelöste Punkt wird pauschal aufgeschlagen: 5 % vom Preis, mindestens 20 €. Daraus entsteht eine Rangfolge nach effektivem Preis, und die Empfehlung erklärt den Unterschied in einem Satz.
- **Weiche Fenstergrenzen:** `flex_days` erlaubt Tage außerhalb des Fensters. Jeder solche Tag kostet Aufschlag, auch wenn Vorlieben egal sind.
- **Buchungszeitpunkt aus Regeln:** eigene Preisreihe (aus früheren Läufen), Google-Preisspanne von SerpApi, Trend der letzten 14 Tage, Abstand zum Abflug und Ferienzeiten (Ostern, Weihnachten, Sommer). Ergebnis: `BUY_NOW`, `WATCH` oder `WAIT` mit spätestem Buchungsdatum und Zielpreis.
- **Optional KI-Einschätzung** (Gemini mit Google-Suche oder Groq ohne Suche). Sie bekommt nur gemessene Preise. Jeder Euro-Betrag in ihrer Begründung muss bis auf 2 € einem gemessenen Preis entsprechen, sonst wird die Antwort verworfen. Regel- und KI-Einschätzung werden beide angezeigt.
- **Web-App** (`web/`): React-PWA mit Login per Magic Link über Supabase. Zeigt Übersicht und Verlauf und erlaubt, den Trip zu bearbeiten.

## Einrichtung

Benötigt: ein Supabase-Konto, ein GitHub-Repository mit Actions und Pages, ein SerpApi-Key. Der Gemini-Key ist optional, aber empfohlen.

1. **Supabase-Projekt anlegen.** Das Schema aus `supabase/migrations/20261108000000_init.sql` einspielen, entweder im SQL-Editor oder mit `supabase db push`. Danach die eigene E-Mail als Admin eintragen (`deine@mail.de` durch die eigene Adresse ersetzen):

   ```sql
   insert into public.app_admins (email) values ('deine@mail.de');
   ```

   Die Anmeldung für neue Nutzer wird erst nach dem ersten Login deaktiviert, siehe Schritt 7.

2. **SerpApi-Key** unter [serpapi.com/manage-api-key](https://serpapi.com/manage-api-key) holen. Der kostenlose Plan umfasst 250 Suchen im Monat.

3. **Gemini-Key** in [Google AI Studio](https://aistudio.google.com/apikey) erzeugen. Hinweis: Ein Google-AI-Plus-Abo enthält keinen API-Zugang. Die API läuft über ein kostenloses Kontingent, und Eingaben können zur Verbesserung von Google-Produkten genutzt werden. Optional geht auch ein Groq-Key (`GROQ_API_KEY`).

4. **GitHub Secrets und Variablen** unter *Settings → Secrets and variables → Actions* anlegen:
   - Secrets: `SERPAPI_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `GEMINI_API_KEY`, optional `GROQ_API_KEY`
   - Variablen: `LLM_PROVIDER` (`gemini` ist Standard, außerdem `groq` oder `none`), `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY`

5. **GitHub Pages** unter *Settings → Pages → Build and deployment → Source* auf **GitHub Actions** stellen. `pages.yml` baut und veröffentlicht die Web-App bei jedem Push auf `master`, der `web/` ändert.

6. **Edge Function deployen:**

   ```bash
   supabase functions deploy trigger-search
   supabase secrets set GITHUB_REPO=<owner>/flight-engine GITHUB_TOKEN=<token>
   ```

   `GITHUB_TOKEN` ist ein Fine-grained Personal Access Token, beschränkt auf dieses Repository, mit der Berechtigung *Actions: Read and write*. Die Funktion startet den Workflow `track.yml` über die Web-App, nur für eingeloggte Admins.

7. **Supabase Auth konfigurieren.** Unter *Authentication → URL Configuration* die Pages-Adresse `https://<github-user>.github.io/flight-engine/` als Site URL und Redirect-URL eintragen. Dann einmal per Magic Link einloggen und anschließend unter *Sign In / Providers* die Option „Allow new users to sign up“ ausschalten, damit sich sonst niemand anmelden kann.

## Benutzung

### CLI (lokal)

Voraussetzung: Python 3.10+.

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # SERPAPI_KEY eintragen
python cli.py run trips/kapverden_2027.yaml
python cli.py track trips/kapverden_2027.yaml --no-llm
```

- `run`: volle Suche, Empfehlung wird gespeichert. Ergebnis als JSON auf stdout, Zusammenfassung auf stderr.
- `track`: nur die Top-Optionen der letzten Empfehlung neu abfragen. Gibt es noch keine Empfehlung, verhält sich `track` wie `run`.
- Optionen: `--budget N` (max. echte Calls), `--no-llm` (KI-Zweitmeinung auslassen), `--trip-from-db NAME` (Profil aus der Datenbank statt aus der YAML).
- Ohne `SUPABASE_URL` läuft ein In-Memory-Speicher. Dann bleibt nach dem Prozessende nichts erhalten.
- Exit-Codes: `0` ok (auch bei zu knappem Budget), `1` SerpApi-Fehler, `2` Key fehlt, `3` keine Flüge, `4` ungültiges Profil.

Tests (SerpApi, Supabase und LLM sind gemockt, kein Key nötig):

```bash
pip install -r requirements-dev.txt
pytest
```

### Suche manuell starten

GitHub → *Actions* → **Track flights** → *Run workflow*. Angeben: Trip-Name (Standard `kapverden_2027`) und Modus `track` (Standard) oder `run`. Die Suche läuft außerdem automatisch alle 3 Tage um 06:17 UTC mit `track`.

### Web-App

Lokal: `cd web`, `cp .env.example .env`, Werte eintragen, dann `npm ci` und `npm run dev`. Veröffentlicht wird über GitHub Pages (Schritt 5 oben).

## SerpApi-Kontingent

- Kostenloser Plan: rund 250 Suchen im Monat. Die Grenze steht in `SERPAPI_MONTHLY_LIMIT` (Standard 250).
- Grobe Kosten: eine volle Suche (`run`) braucht etwa 60 Calls. `track` braucht etwa 3 bis 15 Calls (Standard 15). Der Cron-Lauf alle 3 Tage kommt damit auf rund 150 Calls im Monat, manuelle Läufe kommen dazu.
- Budget-Logik (`cli.py`): Verfügbar ist `min(angefragt, Limit − bereits verbraucht − 20)`. Die 20 Calls bleiben immer als Puffer frei. Liegt das Budget unter 10, wird nichts gesucht, und die CLI meldet das und endet mit Exit-Code 0.
- Der Verbrauch wird pro Call in der Tabelle `api_usage` protokolliert.

## Einzelsuche (alt)

Die ältere Einzelsuche prüft pro Anfrage genau ein Hin- und Rückflug-Datum. Sie läuft über die FastAPI-App in `main.py` und ist nicht Teil des Reisefensters. Sie bleibt zum Vergleich und für schnelle Abfragen erhalten.

```bash
pip install -r requirements.txt
cp .env.example .env              # SERPAPI_KEY eintragen
uvicorn main:app --reload
```

Hinweis: Benötigt wird das Paket `google-search-results` (importiert als `serpapi`), nicht das Paket `serpapi`.

### `GET /search`

| Parameter        | Typ    | Pflicht | Beschreibung                                            |
|------------------|--------|---------|---------------------------------------------------------|
| `origin`         | string | ja      | Abflug: IATA-Code(s) oder bekannte Stadt, kommagetrennt |
| `destination`    | string | ja      | Ziel, wie `origin`                                      |
| `departure_date` | string | ja      | `YYYY-MM-DD`, nicht in der Vergangenheit                |
| `return_date`    | string | bei `roundtrip` | `YYYY-MM-DD`, nicht vor `departure_date`        |
| `trip_type`      | enum   | nein    | `oneway` (Standard) oder `roundtrip`                    |

```bash
curl "http://localhost:8000/search?origin=frankfurt&destination=london&departure_date=2026-12-10"
```

Die Antwort enthält `best_flight`, `cheapest_flight`, `score`, `decision`, `all_flights` und `simulations`.

Fehlercodes: `400` ungültige Eingabe (Datum, Flughafen, fehlendes `return_date`), `404` keine Flüge gefunden, `422` ungültiger `trip_type`, `502` SerpApi nicht erreichbar oder meldet einen Fehler, `503` `SERPAPI_KEY` nicht gesetzt.

### Empfehlung der Einzelsuche

Die Logik steht in `services/decision.py`:

1. *Marktvergleich:* Der Preis des „besten“ Flugs (höchster Score aus Preis, Dauer und Stopps, nicht unbedingt der billigste) wird mit dem Durchschnitt der günstigsten rund 30 % der gefundenen Flüge verglichen. Bei weniger als 4 Flügen ist die Basis der Durchschnitt aller Flüge.
2. *Historischer Vergleich:* Liefert SerpApi `price_insights.price_history`, wird der Preis mit dem Durchschnitt dieser Historie verglichen. Fehlt sie, zählt dieser Teil als 0.
3. Beide Abweichungen (in %) werden 60 / 40 gewichtet. Feste Schwellen (> 15 `BUY`, > 5 `GOOD`, > −5 `OK`, sonst `WAIT`) ergeben die Empfehlung.

Jede Suche löst 5 SerpApi-Abfragen aus (1 Hauptsuche + 4 Nachbartage). Preise werden in EUR abgefragt.

## Status und Grenzen

- **Keine Prognose.** `WAIT` heißt nur: Der Preis liegt aktuell über dem Vergleichswert. Es heißt nicht, dass er sinkt, und es gibt dafür keine Garantie.
- **Regeln und Gewichte sind von Hand gewählt** (Aufschläge, Schwellen, Ferienzeiten). Sie sind nicht an echten Daten kalibriert. Zufallswerte werden nicht verwendet.
- **One-Way-Kombination ist eine Annahme.** Hin- und Rückflug werden als zwei getrennte Tickets zusammengesetzt. Verpasst man einen Anschluss zwischen zwei getrennten Tickets, gibt es keinen Schutz. Bei Direktflügen ist das unkritisch.
- **Round-Trip-Parsing** (`search_roundtrip`) ist nur gegen nachgebaute Beispielantworten getestet, nicht gegen die echte API.
- **Google-Preisspanne** kommt je One-Way-Tag. Für Hin- und Rückflug wird sie addiert. Das ist eine Näherung.
- **Preisdaten** stammen von SerpApi bzw. Google Flights. Historie und Spanne fehlen nicht immer. Die eigene Preisreihe wächst erst mit den Läufen; bei weniger als 5 Datenpunkten ohne Insights ist die Einschätzung nur `WATCH` mit niedriger Konfidenz.
- **Die KI kann irren.** Euro-Beträge werden geprüft, aber Begründungen bleiben Text. Deshalb werden Regel- und KI-Einschätzung beide angezeigt.
- **Nichts wird automatisch gebucht.** Verfügbarkeit und Buchbarkeit liegen bei SerpApi bzw. Google Flights.

## Projektstruktur

```
cli.py                     Einstieg: run / track (auch für den Cron-Lauf)
main.py                    Alte FastAPI-Einzelsuche (/search)
services/
  trip_profile.py          Trip-YAML laden und validieren
  search_planner.py        Tage auswählen, One-Way kombinieren, Budget
  flight_api.py            SerpApi-Aufrufe, Cache, Parsing
  utility.py               Aufschläge für Vorlieben, effektiver Preis, Erklärung
  booking_timing.py        Regelbasierte Kauf-Empfehlung
  llm.py, llm_advisor.py   Gemini/Groq-Anbindung, Prüfung der KI-Antwort
  engine.py                run_trip / track_trip: Ablauf der Läufe
  storage/                 In-Memory- oder Supabase-Speicher
  airports.py, scoring.py, decision.py, simulation.py   Städte, Einzelsuche (alt)
trips/                     Trip-Profile (YAML)
web/                       React + Vite PWA (Supabase-Login)
supabase/                  Migration (Schema, RLS) und Edge Function trigger-search
.github/workflows/         tests.yml, track.yml (Suche), pages.yml (Web-Deploy)
docs/ARCHITECTURE.md       Verbindlicher Modulvertrag
tests/                     pytest (alles gemockt)
```
