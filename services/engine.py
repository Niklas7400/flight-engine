"""Engine: Suche -> Beste Option -> Timing -> Recommendation (gespeichert)."""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Callable, Optional

from services.booking_timing import advise, is_holiday_period
from services.flight_api import NoFlightsError
from services.models import (PriceInsights, Recommendation, TimingAdvice, TripOption,
                             TripProfile, to_jsonable)
from services.search_planner import PROVIDER, plan_and_search, track_with_insights
from services.trip_profile import profile_to_dict
from services.utility import explain_choice

logger = logging.getLogger(__name__)

MONTHLY_LIMIT = 250
TOP_OBSERVATIONS = 15
N_ALTERNATIVES = 5


def _sum(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return a + b if a is not None and b is not None else None


def combined_insights(option: TripOption,
                      insights: dict[tuple[str, date], PriceInsights]) -> Optional[PriceInsights]:
    """Google-Insights gelten je One-Way-Tag. Fuer Hin+Rueck werden beide Tage addiert,
    damit die Spanne zum Gesamtpreis passt. Fehlt eine Seite -> None (kein Vergleich
    mit falscher Groessenordnung)."""
    out = insights.get(("out", option.outbound.departure.date()))
    ret = insights.get(("ret", option.inbound.departure.date()))
    if out is None or ret is None:
        return None
    level = out.price_level if out.price_level == ret.price_level else None
    return PriceInsights(
        lowest_price=_sum(out.lowest_price, ret.lowest_price),
        price_level=level,
        typical_low=_sum(out.typical_low, ret.typical_low),
        typical_high=_sum(out.typical_high, ret.typical_high),
        history=[],  # Verlaeufe beider Tage sind nicht zeitgleich -> nicht addierbar
    )


def _evaluate(profile: TripProfile, storage, options: list[TripOption],
              insights: dict[tuple[str, date], PriceInsights], today: date, calls_start: int,
              llm_timing_fn: Optional[Callable]) -> Recommendation:
    if not options:
        raise NoFlightsError("Keine Flugoptionen gefunden")
    best = options[0]
    cheapest = min(options, key=lambda o: o.price)
    alternatives = options[1:1 + N_ALTERNATIVES]

    trip_id = storage.upsert_trip(profile.name, profile_to_dict(profile))
    now = datetime.now()
    storage.add_observations(trip_id, options[:TOP_OBSERVATIONS], now)
    # Preisreihe genau dieser Option (gleiche Tage + Flughaefen); erst bei zu wenig
    # Punkten auf das Tagesminimum aller Optionen ausweichen.
    series = storage.get_price_series(trip_id, best.key)
    if len(series) < 3:
        series = storage.get_best_price_series(trip_id)
    day_insights = combined_insights(best, insights)
    rules = advise(best, series, day_insights, today,
                   is_holiday_period(best.outbound.departure.date()))

    timing_llm: Optional[TimingAdvice] = None
    if llm_timing_fn is not None:
        try:
            timing_llm = llm_timing_fn(best, series, day_insights, rules, today)
        except Exception as exc:  # LLM darf die Empfehlung nie verhindern
            logger.warning("LLM-Timing fehlgeschlagen: %s", type(exc).__name__)

    used = storage.api_calls_this_month(PROVIDER)
    rec = Recommendation(
        trip_name=profile.name, created_at=now, best=best, cheapest=cheapest,
        alternatives=alternatives, explanation=explain_choice(best, cheapest, wanted_nights=profile.nights),
        timing_rules=rules, timing_llm=timing_llm,
        api_calls_used=used - calls_start,
        api_calls_remaining_month=MONTHLY_LIMIT - used,
    )
    storage.save_recommendation(trip_id, to_jsonable(rec))
    return rec


def run_trip(profile: TripProfile, storage, budget: int, today: Optional[date] = None,
             llm_timing_fn: Optional[Callable] = None) -> Recommendation:
    today = today or date.today()
    start = storage.api_calls_this_month(PROVIDER)
    options, insights = plan_and_search(profile, storage, budget, today)
    return _evaluate(profile, storage, options, insights, today, start, llm_timing_fn)


def track_trip(profile: TripProfile, storage, budget: int, today: Optional[date] = None,
               llm_timing_fn: Optional[Callable] = None) -> Recommendation:
    today = today or date.today()
    start = storage.api_calls_this_month(PROVIDER)
    trip = storage.get_trip(profile.name)
    last = storage.latest_recommendation(trip["id"]) if trip else None
    if not last:
        return run_trip(profile, storage, budget, today, llm_timing_fn)
    keys = []
    for o in [last.get("best"), last.get("cheapest"), *(last.get("alternatives") or [])]:
        if isinstance(o, dict) and o.get("key"):
            keys.append(o["key"])
    keys = list(dict.fromkeys(keys))
    options, insights = track_with_insights(profile, storage, keys, budget, today) if keys else ([], {})
    if not options:
        used = storage.api_calls_this_month(PROVIDER) - start
        return run_trip(profile, storage, max(budget - used, 0), today, llm_timing_fn)
    return _evaluate(profile, storage, options, insights, today, start, llm_timing_fn)
