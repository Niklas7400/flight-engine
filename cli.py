"""Kommandozeile: Flugsuche und Tracking ohne Webserver.

Aufrufe:
    python cli.py run trips/kapverden_2027.yaml [--budget N] [--no-llm]
    python cli.py track trips/kapverden_2027.yaml [--budget N] [--no-llm]
    python cli.py run --trip-from-db kapverden_2027
    python cli.py track-all [--budget N] [--no-llm]

Vertrag: docs/ARCHITECTURE.md (Abschnitt cli.py).
Ausgabe: JSON auf stdout, kurze Zusammenfassung auf stderr.
Exit-Codes: 0 ok (auch bei zu knappem Budget), 1 SerpApi-Fehler,
2 API-Key fehlt, 3 keine Fluege, 4 ungueltiges Profil.

Engine, LLM und Storage werden erst innerhalb der Funktionen importiert,
damit diese Datei schon importierbar ist, bevor alle Module existieren.
"""
from __future__ import annotations

import argparse
import calendar
import json
import logging
import os
import sys
from datetime import date
from typing import Optional

DEFAULT_BUDGET = {"run": 60, "track": 15}
DEFAULT_MONTHLY_LIMIT = 250
MONTHLY_BUFFER = 20
MIN_BUDGET = 10
TRACK_ALL_DEFAULT_BUDGET = 30
MIN_TRIP_BUDGET = 3
RUN_INTERVAL_DAYS = 3
PROVIDER = "serpapi"

EXIT_OK = 0
EXIT_UPSTREAM = 1
EXIT_MISSING_KEY = 2
EXIT_NO_FLIGHTS = 3
EXIT_PROFILE = 4

logger = logging.getLogger("cli")


def monthly_limit() -> int:
    """SERPAPI_MONTHLY_LIMIT aus der Umgebung, sonst 250."""
    raw = os.environ.get("SERPAPI_MONTHLY_LIMIT")
    if not raw:
        return DEFAULT_MONTHLY_LIMIT
    try:
        return int(raw)
    except ValueError:
        logger.warning("SERPAPI_MONTHLY_LIMIT=%r ist keine Zahl, nehme %d", raw, DEFAULT_MONTHLY_LIMIT)
        return DEFAULT_MONTHLY_LIMIT


def effective_budget(requested: int, used_this_month: int, limit: int) -> int:
    """min(requested, limit - used - 20). Unter MIN_BUDGET wird nicht gesucht."""
    return min(requested, limit - used_this_month - MONTHLY_BUFFER)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py", description="Flugsuche und Buchungsempfehlung (CLI)."
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="{run,track,track-all}")
    for name, help_text in (
        ("run", "Volle Suche, Empfehlung speichern."),
        ("track", "Nur Top-Optionen der letzten Empfehlung neu abfragen."),
    ):
        p = sub.add_parser(name, help=help_text, description=help_text)
        p.add_argument(
            "trip", nargs="?",
            help="Pfad zur Trip-YAML, z. B. trips/kapverden_2027.yaml (optional mit --trip-from-db)",
        )
        p.add_argument(
            "--budget",
            type=int,
            default=None,
            help=f"Max. echte API-Calls (Standard {DEFAULT_BUDGET[name]} fuer {name})",
        )
        p.add_argument("--no-llm", action="store_true", help="LLM-Zweitmeinung auslassen")
        p.add_argument(
            "--trip-from-db",
            metavar="NAME",
            help="Profil aus der Datenbank laden statt aus YAML (hat Vorrang)",
        )
    p = sub.add_parser(
        "track-all", help="Alle Trips mit aktiviertem Tracking aus der DB aktualisieren.",
        description="Alle Trips mit aktiviertem Tracking aus der DB aktualisieren.",
    )
    p.add_argument(
        "--budget", type=int, default=TRACK_ALL_DEFAULT_BUDGET,
        help=f"Max. echte API-Calls pro Lauf ueber alle Trips (Standard {TRACK_ALL_DEFAULT_BUDGET})",
    )
    p.add_argument("--no-llm", action="store_true", help="LLM-Zweitmeinung auslassen")
    return parser


def track_all_budget(requested: int, used: int, limit: int, today: date, n_trips: int) -> tuple[int, int]:
    """(Lauf-Budget, Budget pro Trip). Verteilt den Rest des Monats auf die verbleibenden Laeufe."""
    remaining = limit - used - MONTHLY_BUFFER
    days_left = calendar.monthrange(today.year, today.month)[1] - today.day
    runs_left = max(1, days_left // RUN_INTERVAL_DAYS + 1)
    run_budget = min(requested, remaining // runs_left)
    per_trip = max(run_budget // n_trips, 0) if n_trips else 0
    return run_budget, per_trip


def _execute_all(args: argparse.Namespace, today: Optional[date] = None) -> int:
    from services.storage import get_storage
    from services.trip_profile import profile_from_dict

    storage = get_storage()
    today = today or date.today()
    trips = []
    for row in storage.list_trips(tracking_only=True):
        try:
            trips.append((row["name"], profile_from_dict(row["profile"])))
        except Exception as exc:  # ungueltiges Profil: loggen, ueberspringen
            logger.warning("Trip '%s' hat ein ungueltiges Profil und wird uebersprungen: %s", row["name"], exc)
    if not trips:
        print("Keine Trips mit aktiviertem Tracking.", file=sys.stderr)
        return EXIT_OK

    used = storage.api_calls_this_month(PROVIDER)
    limit = monthly_limit()
    run_budget, per_trip = track_all_budget(args.budget, used, limit, today, len(trips))
    logger.info(
        "SerpApi: %d von %d Calls, Lauf-Budget %d, %d Trips, je %d Calls",
        used, limit, run_budget, len(trips), per_trip,
    )
    if per_trip < MIN_TRIP_BUDGET:
        print(
            f"Budget zu knapp ({per_trip} Calls pro Trip, Minimum {MIN_TRIP_BUDGET}): "
            f"keine Suche. Monat: {used} von {limit} verbraucht.",
            file=sys.stderr,
        )
        return EXIT_OK

    llm_fn = None
    if not args.no_llm:
        from services.llm_advisor import llm_timing

        llm_fn = llm_timing

    from services.engine import track_trip
    from services.flight_api import MissingApiKeyError, NoFlightsError, UpstreamError

    ok = 0
    for name, profile in trips:
        try:
            rec = track_trip(profile, storage, per_trip, today=today, llm_timing_fn=llm_fn)
        except MissingApiKeyError as exc:
            print(f"SERPAPI_KEY fehlt: {exc}", file=sys.stderr)
            return EXIT_MISSING_KEY
        except (NoFlightsError, UpstreamError, ValueError) as exc:
            print(f"[{name}] Fehler: {exc}", file=sys.stderr)
            continue
        ok += 1
        print(f"[{name}]", file=sys.stderr)
        for line in _summary(rec, profile.currency):
            print(f"  {line}", file=sys.stderr)
    return EXIT_OK if ok else EXIT_UPSTREAM


def _load_profile(args: argparse.Namespace, storage):
    from services.trip_profile import load_profile, profile_from_dict

    if args.trip_from_db:
        row = storage.get_trip(args.trip_from_db)
        if row is None and args.trip:
            # Erster Lauf: Trip noch nicht in der DB -> YAML als Startwert
            return load_profile(args.trip)
        if row is None:
            raise ValueError(f"Trip '{args.trip_from_db}' ist in der Datenbank nicht vorhanden.")
        return profile_from_dict(row["profile"])
    if not args.trip:
        raise ValueError("Keine Trip-Datei angegeben (YAML-Pfad oder --trip-from-db NAME).")
    return load_profile(args.trip)


def _leg_text(leg) -> str:
    return (
        f"{leg.origin}-{leg.destination} {leg.departure.strftime('%d.%m.%Y %H:%M')}"
        f" ({leg.stops} Stopp{'s' if leg.stops != 1 else ''})"
    )


def _summary(rec, currency: str) -> list[str]:
    best = rec.best
    timing = rec.timing_llm or rec.timing_rules
    action = getattr(timing.action, "value", timing.action)
    lines = [
        f"Bester Flug: hin {_leg_text(best.outbound)}, zurueck {_leg_text(best.inbound)}",
        f"Preis: {best.price:.2f} {currency} fuer {best.nights} Naechte",
    ]
    line = f"Empfehlung: {action}"
    if timing.decide_by:
        line += f", spaetestens buchen bis {timing.decide_by.strftime('%d.%m.%Y')}"
    if timing.target_price is not None:
        line += f", Zielpreis {timing.target_price:.2f} {currency}"
    line += f" (Quelle: {timing.source})"
    lines.append(line)
    if rec.explanation:
        lines.append(rec.explanation)
    return lines


def _execute(args: argparse.Namespace) -> int:
    from services.storage import get_storage

    storage = get_storage()
    try:
        profile = _load_profile(args, storage)
    except ValueError as exc:
        print(f"Fehler im Trip-Profil: {exc}", file=sys.stderr)
        return EXIT_PROFILE

    used = storage.api_calls_this_month(PROVIDER)
    limit = monthly_limit()
    requested = args.budget if args.budget is not None else DEFAULT_BUDGET[args.command]
    budget = effective_budget(requested, used, limit)
    logger.info(
        "SerpApi: %d Calls diesen Monat von %d, Budget fuer diesen Lauf: %d",
        used, limit, budget,
    )
    if budget < MIN_BUDGET:
        print(
            f"Budget zu knapp ({budget} Calls, Minimum {MIN_BUDGET}): "
            f"keine Suche. Monat: {used} von {limit} verbraucht.",
            file=sys.stderr,
        )
        return EXIT_OK

    llm_fn = None
    if not args.no_llm:
        from services.llm_advisor import llm_timing

        llm_fn = llm_timing

    from services.engine import run_trip, track_trip
    from services.flight_api import MissingApiKeyError, NoFlightsError, UpstreamError
    from services.models import to_jsonable

    runner = run_trip if args.command == "run" else track_trip
    try:
        rec = runner(profile, storage, budget, llm_timing_fn=llm_fn)
    except MissingApiKeyError as exc:
        print(f"SERPAPI_KEY fehlt: {exc}", file=sys.stderr)
        return EXIT_MISSING_KEY
    except NoFlightsError as exc:
        print(f"Keine Fluege gefunden: {exc}", file=sys.stderr)
        return EXIT_NO_FLIGHTS
    except UpstreamError as exc:
        print(f"SerpApi meldet einen Fehler: {exc}", file=sys.stderr)
        return EXIT_UPSTREAM
    except ValueError as exc:
        print(f"Fehler im Trip-Profil: {exc}", file=sys.stderr)
        return EXIT_PROFILE

    print(json.dumps(to_jsonable(rec), ensure_ascii=False, indent=2))
    for line in _summary(rec, profile.currency):
        print(line, file=sys.stderr)
    return EXIT_OK


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parser().parse_args(argv)
    if args.command == "track-all":
        return _execute_all(args)
    return _execute(args)


if __name__ == "__main__":
    sys.exit(main())
