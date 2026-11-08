"""Utility-Scoring: Strafen pro Verbindung, effektiver Preis, Erklaerung (deutsch).

Vertrag: docs/ARCHITECTURE.md, Abschnitt `services/utility.py`.
"""
from __future__ import annotations

from datetime import time
from typing import Optional

from services.models import Leg, Preferences, TripOption, TripProfile

EARLY_DEPARTURE_BEFORE = time(7, 0)
LATE_ARRIVAL_FROM = time(23, 0)
LATE_ARRIVAL_BEFORE = time(5, 0)
LONG_DURATION_FACTOR = 1.5
LONG_DURATION_EXTRA_MIN = 180
MIN_PENALTY_EUR = 20.0
PENALTY_SHARE = 0.05


def penalty_unit(price: float) -> float:
    """Strafe pro ausgeloestem Punkt: max(20 EUR, 5 % des Preises)."""
    return round(max(MIN_PENALTY_EUR, PENALTY_SHARE * price), 2)


def _leg_hits(leg: Leg, direction: str, fastest: int, prefs: Preferences) -> list[str]:
    """Keys der ausgeloesten Regeln fuer eine Richtung ("out" | "in")."""
    hits: list[str] = []
    dep_t = leg.departure.time()
    arr_t = leg.arrival.time()
    if prefs.avoid_early_departure and dep_t < EARLY_DEPARTURE_BEFORE:
        hits.append(f"early_departure_{direction}")
    if prefs.avoid_late_arrival and (arr_t >= LATE_ARRIVAL_FROM or arr_t < LATE_ARRIVAL_BEFORE):
        hits.append(f"late_arrival_{direction}")
    if prefs.avoid_stops and leg.stops > 0:
        hits.append(f"stops_{direction}")
    if prefs.avoid_long_duration and (
        leg.duration_min > LONG_DURATION_FACTOR * fastest
        or leg.duration_min > fastest + LONG_DURATION_EXTRA_MIN
    ):
        hits.append(f"long_duration_{direction}")
    return hits


def apply_utility(options: list[TripOption], profile: TripProfile) -> list[TripOption]:
    """Fuellt `penalties` und `effective_cost` und sortiert aufsteigend (effective_cost, price)."""
    if not options:
        return []

    fastest = {
        "out": min(o.outbound.duration_min for o in options),
        "in": min(o.inbound.duration_min for o in options),
    }
    prefs = profile.preferences

    for opt in options:
        counts: dict[str, int] = {}

        def bump(key: str, n: int = 1) -> None:
            if n > 0:
                counts[key] = counts.get(key, 0) + n

        for key in _leg_hits(opt.outbound, "out", fastest["out"], prefs):
            bump(key)
        for key in _leg_hits(opt.inbound, "in", fastest["in"], prefs):
            bump(key)

        # Immer aktiv (weiche Fenstergrenze): Tage ausserhalb des Reisefensters.
        bump("outside_window", (profile.earliest_departure - opt.outbound.departure.date()).days)
        bump("outside_window", (opt.inbound.departure.date() - profile.latest_return).days)

        if prefs.avoid_length_deviation:
            bump("length_deviation", abs(opt.nights - profile.nights))

        unit = penalty_unit(opt.price)
        opt.penalties = {key: round(n * unit, 2) for key, n in counts.items()}
        opt.effective_cost = round(opt.price + sum(opt.penalties.values()), 2)

    return sorted(options, key=lambda o: (o.effective_cost, o.price))


def _join(parts: list[str]) -> str:
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " und " + parts[-1]


def _hours(minutes: int) -> int:
    return round(minutes / 60)


def _reason(
    key: str,
    best: TripOption,
    cheapest: TripOption,
    wanted_nights: Optional[int],
) -> Optional[str]:
    """Ein lesbarer Satzteil, warum `best` gegenueber `cheapest` besser ist."""
    if key == "early_departure_out":
        return (f"Hinflug um {best.outbound.departure:%H:%M} "
                f"statt {cheapest.outbound.departure:%H:%M}")
    if key == "early_departure_in":
        return (f"Rückflug um {best.inbound.departure:%H:%M} "
                f"statt {cheapest.inbound.departure:%H:%M}")
    if key == "late_arrival_out":
        return (f"Hinflug-Ankunft um {best.outbound.arrival:%H:%M} "
                f"statt {cheapest.outbound.arrival:%H:%M}")
    if key == "late_arrival_in":
        return (f"Rückflug-Ankunft um {best.inbound.arrival:%H:%M} "
                f"statt {cheapest.inbound.arrival:%H:%M}")
    if key == "stops_out":
        if best.outbound.stops == 0:
            return "Hinflug direkt statt mit Umstieg"
        return "Hinflug mit weniger Umstiegen"
    if key == "stops_in":
        if best.inbound.stops == 0:
            return "Rückflug direkt statt mit Umstieg"
        return "Rückflug mit weniger Umstiegen"
    if key == "long_duration_out":
        return (f"kürzere Hinreisezeit ({_hours(best.outbound.duration_min)} h "
                f"statt {_hours(cheapest.outbound.duration_min)} h)")
    if key == "long_duration_in":
        return (f"kürzere Rückreisezeit ({_hours(best.inbound.duration_min)} h "
                f"statt {_hours(cheapest.inbound.duration_min)} h)")
    if key == "length_deviation":
        if wanted_nights is not None:
            return f"näher an {wanted_nights} Nächten"
        return "näher an der Wunschdauer"
    if key == "outside_window":
        return "Reisedaten innerhalb des Fensters statt außerhalb"
    return None


def explain_choice(
    best: TripOption,
    cheapest: TripOption,
    wanted_nights: Optional[int] = None,
) -> str:
    """Deutsche Begruendung, warum `best` statt `cheapest` empfohlen wird.

    `wanted_nights` (profile.nights) ist optional; nur fuer den Text "naeher an N Naechten".
    Erwartet, dass `apply_utility` vorher lief (Penalties gesetzt).
    """
    # TripOption.key enthaelt nur Datum + Flughaefen, nicht die Uhrzeit. Zwei Fluege am
    # selben Tag mit denselben Flughaefen haben also denselben key -> zusaetzlich Legs vergleichen.
    same = (
        best.key == cheapest.key
        and (best.outbound, best.inbound, best.price)
        == (cheapest.outbound, cheapest.inbound, cheapest.price)
    )
    if same:
        return "Die beste Option ist auch die günstigste."

    diff = round(best.price - cheapest.price)
    reasons: list[str] = []
    for key in cheapest.penalties:
        if key in best.penalties:
            continue
        text = _reason(key, best, cheapest, wanted_nights)
        if text:
            reasons.append(text)
    if not reasons:
        reasons = ["insgesamt weniger Einschränkungen"]
    return f"Für {diff} € mehr: {_join(reasons)}."
