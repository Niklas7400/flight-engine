# Flight Engine

Kleine FastAPI-Anwendung für die Flugsuche mit einer Kauf-oder-Warte-Einschätzung. Die Flugdaten kommen von [SerpApi (Google Flights)](https://serpapi.com/google-flights-api).

> **Status: Prototyp.** Das Projekt ist als Wochenendprojekt entstanden. Die Empfehlung ist eine einfache Heuristik und keine validierte Preisprognose, siehe [Status und Grenzen](#status-und-grenzen).

## Was es tut

- Flugsuche für One-Way und Round-Trip
- Städtenamen werden zu mehreren Flughäfen aufgelöst (z. B. `london` → `LHR,LGW,STN,LTN`, siehe `services/airports.py`)
- Bewertung der Flüge nach Preis, Dauer und Anzahl Stopps
- Einschätzung `BUY` / `GOOD` / `OK` / `WAIT` mit kurzer Begründung
- Preise für die Nachbartage (±1 und ±2 Tage) zum Vergleich

## Setup

Voraussetzungen: Python 3.10+ und ein [SerpApi-Key](https://serpapi.com/manage-api-key).

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt        # zum Testen: requirements-dev.txt
cp .env.example .env                   # dann SERPAPI_KEY in .env eintragen
uvicorn main:app --reload
```

Hinweis: Benötigt wird das Paket `google-search-results` (importiert als `serpapi`), nicht das Paket `serpapi`.

Tests (SerpApi wird gemockt, kein Key nötig):

```bash
pip install -r requirements-dev.txt
pytest
```

## API

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

## Projektstruktur

```
main.py                  FastAPI-App, Eingabevalidierung, Fehlerbehandlung
services/airports.py     Städte → Flughafencodes
services/flight_api.py   SerpApi-Aufruf und Parsing der Flüge
services/scoring.py      Bewertung einzelner Flüge
services/decision.py     Kauf-oder-Warte-Heuristik
services/simulation.py   Preise für Nachbartage
tests/                   pytest-Tests (SerpApi gemockt)
```

## Status und Grenzen

**Wie die Empfehlung berechnet wird** (`services/decision.py`):

1. *Marktvergleich:* Der Preis des „besten“ Flugs (höchster Score aus Preis, Dauer, Stopps, nicht unbedingt der billigste) wird mit dem Durchschnitt der günstigsten ca. 30 % der aktuell gefundenen Flüge verglichen. Bei weniger als 4 Flügen ist die Basis der Durchschnitt aller Flüge.
2. *Historischer Vergleich:* Falls SerpApi `price_insights.price_history` mitliefert, wird der Preis mit dem Durchschnitt dieser Historie verglichen. Fehlt sie, zählt dieser Teil als 0.
3. Beide Abweichungen (in %) werden 60 / 40 gewichtet. Feste Schwellen (>15 `BUY`, >5 `GOOD`, >−5 `OK`, sonst `WAIT`) ergeben die Empfehlung.

Die Gewichte und Schwellen sind von Hand gewählt und nicht an echten Daten kalibriert oder getestet. Zufallswerte werden nicht verwendet.

**Was das NICHT leistet:**

- Es ist **keine Prognose**. `WAIT` bedeutet nur „aktuell teurer als der Vergleichswert“, nicht „der Preis wird sinken“.
- Es gibt **keine Garantie**, dass ein Preis günstiger oder teurer wird.
- Die Preishistorie stammt von Google Flights über SerpApi und ist nur so aussagekräftig wie diese; sie liegt nicht immer vor und ist nicht auf den einzelnen Flug bezogen.
- Die Nachbartag-„Simulation“ sind echte zusätzliche Abfragen, keine Modellierung.
- Die Round-Trip-Auswertung wurde nur mit nachgebauten Beispielantworten getestet, nicht gegen die echte API. Die Aufteilung in Hin- und Rückflug ist eine Annahme über das Antwortformat.

**Abhängigkeit von SerpApi:** Ohne Key und Kontingent funktioniert nichts. Jede Suche löst **5 SerpApi-Abfragen** aus (1 Hauptsuche + 4 Nachbartage), was das SerpApi-Kontingent schnell aufbrauchen kann. Preise werden in EUR abgefragt; Verfügbarkeit und Genauigkeit der Daten liegen bei SerpApi bzw. Google Flights.
