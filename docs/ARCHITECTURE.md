# Architektur: Flexible Reisesuche mit Buchungsempfehlung

Verbindlicher Vertrag für alle Module. Datentypen: `services/models.py`.

## Ablauf

```
GitHub Actions (cli.py, Cron alle 3 Tage oder workflow_dispatch)
   │  trip_profile → search_planner → utility → booking_timing (+ llm_advisor)
   │  SerpApi (Google Flights), optional Gemini/Groq
   ▼
Supabase (trips, price_observations, recommendations, api_usage, search_cache)
   ▲
web/ (React + Vite PWA, Supabase-Login per Magic Link, liest Ergebnisse, bearbeitet Trip)
```

Geheimnisse (`SERPAPI_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`) nur in
`.env` bzw. GitHub Secrets. Das Frontend kennt nur URL und Publishable Key.

## Module und Signaturen

### `services/trip_profile.py`
- `load_profile(path: str) -> TripProfile` (YAML) und `profile_from_dict(d: dict) -> TripProfile`
- `profile_to_dict(p: TripProfile) -> dict`
- Validierung → `ValueError` mit deutscher Meldung: 1–3 Origins/Destinations (nach
  `resolve_airports`), IATA `^[A-Z]{3}$`, `earliest_departure < latest_return`,
  `min_nights <= nights <= max_nights`, `flex_days` 0–3.
- Alias in `services/airports.py`: `"kapverden"`/`"cape verde"` → `SID,BVC,RAI`.

### `services/flight_api.py` (Refactor, alte `get_flights` bleibt für `/search`)
- `search_oneway(origins: list[str], destinations: list[str], day: date) -> SearchResult`
  Ein SerpApi-Call (`type=2`, kommagetrennte IDs, `currency=EUR`, `adults=1`).
- `search_roundtrip(origins, destinations, out_day: date, ret_day: date) -> list[TripOption]`
  Ein Call `type=1`. Rückflugpreise kommen bei SerpApi über `departure_token` (Folge-Call);
  zulässig ist: nur den Gesamtpreis der Hinflug-Option nehmen und den Rückflug-Leg per
  `departure_token`-Call holen, maximal 1 Folge-Call. Anzahl Calls im Docstring nennen.
- Beide registrieren jeden echten Call über `storage.record_api_call("serpapi", kind)`
  und nutzen `storage.cache_get/cache_set` (Schlüssel = sortierte Parameter ohne api_key,
  TTL 12 h).
- Exceptions wie bisher: `MissingApiKeyError`, `UpstreamError`, `NoFlightsError`.

### `services/storage/` 
- `base.py`: `class Storage(Protocol)` mit
  - `get_trip(name) -> Optional[dict]`, `upsert_trip(name, profile: dict) -> str` (id)
  - `add_observations(trip_id, options: list[TripOption], observed_at)`
  - `get_price_series(trip_id, option_key: str) -> list[PricePoint]`
  - `get_best_price_series(trip_id) -> list[PricePoint]` (Tagesminimum über alle Optionen)
  - `save_recommendation(trip_id, rec: dict)`, `latest_recommendation(trip_id) -> Optional[dict]`
  - `record_api_call(provider, kind)`, `api_calls_this_month(provider) -> int`
  - `cache_get(key) -> Optional[dict]`, `cache_set(key, value: dict)`
- `memory.py`: `MemoryStorage` (Tests, lokal ohne DB)
- `supabase_store.py`: `SupabaseStorage` (supabase-py, Service-Role-Key)
- `get_storage() -> Storage`: Supabase wenn `SUPABASE_URL` gesetzt, sonst Memory.

### `services/search_planner.py`
- `plan_and_search(profile, storage, budget: int) -> tuple[list[TripOption], dict[date, PriceInsights]]`
  1. Hinflug-Tage: `earliest_departure - flex_days` … `latest_return - min_nights + flex_days`
     (nicht vor heute). Rückflug-Tage analog. Pro Tag **ein** `search_oneway` (alle Flughäfen
     in einem Call).
  2. Kombinieren: alle Paare mit `min_nights - flex_days <= nights <= max_nights + flex_days`,
     Rückflug startet am Ankunftsflughafen (außer `allow_open_jaw`).
  3. Utility berechnen, Top 8 per `search_roundtrip` verfeinern, wenn Budget reicht.
  4. Budget: nie mehr als `budget` echte Calls; fehlende Tage überspringen und loggen.
- `track(profile, storage, keys: list[str], budget) -> list[TripOption]`: nur die Tage der
  gegebenen Options-Keys neu abfragen (dedupliziert).

### `services/utility.py`
- `apply_utility(options: list[TripOption], profile) -> list[TripOption]` (sortiert nach
  `effective_cost`, füllt `penalties`)
- Strafe pro ausgelöstem Punkt und pro Leg: `max(20, 0.05 * option.price)` EUR.
  - early_departure: Abflug < 07:00
  - late_arrival: Ankunft >= 23:00 oder < 05:00
  - stops: je Leg mit stops > 0 (einmal pro Leg, nicht pro Stopp)
  - long_duration: Leg-Dauer > 1.5 × schnellste gleicher Richtung ODER > schnellste + 180 min
  - length_deviation: pro Nacht |nights − profile.nights|
  - außerhalb Fenster (flex_days): immer pro Tag, auch wenn Vorlieben egal (weiche Grenze)
- `explain_choice(best, cheapest) -> str`: deutscher Satz, z. B.
  „Für 8 € mehr: Abflug 10:15 statt 05:40 und direkt statt mit Umstieg.“

### `services/booking_timing.py`
- `advise(best: TripOption, series: list[PricePoint], insights: Optional[PriceInsights],
  today: date, holiday_period: bool) -> TimingAdvice`
- Regeln (deterministisch, jede ausgelöste Regel ergibt einen Satz in `reasons`):
  - Preis <= `typical_low` oder <= 20. Perzentil eigener Historie → BUY_NOW
  - Trend 14 Tage > +5 % → Richtung BUY_NOW; < −5 % → WAIT
  - Tage bis Abflug < 45 (bei holiday_period < 75) → BUY_NOW
  - < 5 Datenpunkte und keine Insights → WATCH, confidence low
  - `decide_by`: Abflug − 75 Tage (holiday) bzw. − 45 Tage; `target_price` = 20. Perzentil
    bzw. `typical_low`.
- `is_holiday_period(day: date) -> bool`: Ostern ±10 Tage (Ostersonntag berechnen),
  Weihnachten, Sommer Juli/Aug.

### `services/llm.py` + `services/llm_advisor.py`
- `llm.py`: `complete_json(prompt: str, system: str, use_search: bool) -> Optional[dict]`
  - Provider aus `LLM_PROVIDER` (`gemini` Standard | `groq` | `none`).
  - Gemini: native REST `generateContent`, Modell `GEMINI_MODEL`, bei `use_search` Tool
    `google_search`. Groq: OpenAI-kompatibel, `GROQ_MODEL`, ohne Suche.
  - Kein Key, Timeout, Fehler → `None` (nie Exception nach außen). httpx, Timeout 30 s.
- `llm_advisor.py`: `llm_timing(best, series, insights, rules_advice, today) -> Optional[TimingAdvice]`
  - Übergibt nur gemessene Fakten als JSON. Antwort-Schema
    `{action, decide_by, target_price, confidence, reasons[]}`.
  - Validierung: action im Enum, decide_by zwischen heute und Abflug, target_price > 0,
    jeder €-Betrag in reasons muss ±2 € einem gemessenen Preis entsprechen → sonst `None`.

### `cli.py`
- `python cli.py run trips/kapverden_2027.yaml [--budget N]`: volle Suche → Recommendation
  speichern, JSON ausgeben.
- `python cli.py track trips/kapverden_2027.yaml`: Tracking der Top-Optionen aus letzter
  Empfehlung; wenn keine vorhanden → `run`.
- Budget-Standard: `min(arg, 250 − api_calls_this_month − 20)`; bei < 10 nichts tun.

## Konventionen
- Python 3.10+, Typ-Hints, deutsche Nutzertexte, englische Bezeichner.
- Keine echten Netzwerk-Calls in Tests (SerpApi, Supabase, LLM gemockt).
- Venv für Tests: `/tmp/claude-0/venv` (`. /tmp/claude-0/venv/bin/activate && python -m pytest -q`).
