"""Suchplanung: Tagesabfragen, One-Way-Kombination, Verfeinerung per Roundtrip, Tracking.

Budget = maximale Anzahl ECHTER API-Calls (Cache-Treffer zaehlen nicht). Gezaehlt wird
ueber storage.api_calls_this_month("serpapi") vor/nach den Calls.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

from services.flight_api import (
    MissingApiKeyError,
    NoFlightsError,
    UpstreamError,
    search_oneway,
    search_roundtrip,
)
from services.models import Leg, OneWayOffer, PriceInsights, TripOption, TripProfile
from services.utility import apply_utility

logger = logging.getLogger(__name__)

PROVIDER = "serpapi"
TOP_PER_DAY = 5          # nur die guenstigsten N One-Ways je Tag/Flughafen kombinieren
REFINE_TOP = 8           # so viele Tagespaare per Roundtrip verfeinern
REFINE_CALLS = 2         # search_roundtrip(max_outbound=1): 1 Such- + 1 Folge-Call
REFINE_RESERVE = 16      # bei grossem Budget fuer die Verfeinerung zurueckhalten
RESERVE_MIN_BUDGET = 40


# ---------------------------------------------------------------------------
# Tage
# ---------------------------------------------------------------------------

def _range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)] if end >= start else []


def outbound_days(profile: TripProfile, today: date) -> list[date]:
    """earliest_departure - flex ... latest_return - min_nights + flex, nicht vor heute."""
    start = max(profile.earliest_departure - timedelta(days=profile.flex_days), today)
    end = profile.latest_return - timedelta(days=profile.min_nights) + timedelta(days=profile.flex_days)
    return _range(start, end)


def return_days(profile: TripProfile, today: date) -> list[date]:
    """earliest_departure + min_nights - flex ... latest_return + flex, nicht vor heute."""
    start = max(profile.earliest_departure + timedelta(days=profile.min_nights)
                - timedelta(days=profile.flex_days), today)
    end = profile.latest_return + timedelta(days=profile.flex_days)
    return _range(start, end)


def _spread(days: list[date]) -> list[date]:
    """Gleichmaessige Abdeckung bei knappem Budget: erst jeder 2. Tag, dann die Luecken."""
    return days[::2] + days[1::2]


def _query_order(out_days: list[date], ret_days: list[date]) -> list[tuple[str, date]]:
    o, r = _spread(out_days), _spread(ret_days)
    order: list[tuple[str, date]] = []
    for i in range(max(len(o), len(r))):
        if i < len(o):
            order.append(("out", o[i]))
        if i < len(r):
            order.append(("ret", r[i]))
    return order


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------

class _Budget:
    def __init__(self, storage, budget: int):
        self.storage = storage
        self.limit = max(budget, 0)
        self.start = storage.api_calls_this_month(PROVIDER)

    @property
    def used(self) -> int:
        return storage_calls(self.storage) - self.start

    @property
    def left(self) -> int:
        return self.limit - self.used


def storage_calls(storage) -> int:
    return storage.api_calls_this_month(PROVIDER)


def _oneway(storage, origins, destinations, day):
    """Ein Tag; NoFlights/Upstream -> None (geloggt), MissingApiKey wird durchgereicht."""
    try:
        return search_oneway(list(origins), list(destinations), day, storage)
    except MissingApiKeyError:
        raise
    except (NoFlightsError, UpstreamError) as exc:
        logger.warning("Tag %s %s->%s uebersprungen: %s", day, origins, destinations,
                       type(exc).__name__)
        return None


def _cheapest(offers: list[OneWayOffer], group_attr: Optional[str]) -> list[OneWayOffer]:
    offers = sorted(offers, key=lambda o: o.price)
    if group_attr is None:
        return offers[:TOP_PER_DAY]
    groups: dict[str, list[OneWayOffer]] = {}
    for o in offers:
        g = groups.setdefault(getattr(o.leg, group_attr), [])
        if len(g) < TOP_PER_DAY:
            g.append(o)
    return sorted((o for g in groups.values() for o in g), key=lambda o: o.price)


def _combine(out_offers: list[OneWayOffer], ret_offers: list[OneWayOffer],
             allow_open_jaw: bool, top: bool = True) -> list[TripOption]:
    """Paare eines Tagespaares. top=True: je Flughafen die 5 guenstigsten Hin x Rueck."""
    if top:
        # ohne Open-Jaw je Zielflughafen (Hin) bzw. Startflughafen (Rueck) die 5 billigsten
        out_offers = _cheapest(out_offers, None if allow_open_jaw else "destination")
        ret_offers = _cheapest(ret_offers, None if allow_open_jaw else "origin")
    result = []
    for o in out_offers:
        for r in ret_offers:
            if not allow_open_jaw and r.leg.origin != o.leg.destination:
                continue
            if not o.leg.arrival < r.leg.departure:
                continue
            tokens = [t for t in (o.booking_token, r.booking_token) if t]
            result.append(TripOption(outbound=o.leg, inbound=r.leg, price=o.price + r.price,
                                     source="oneway_combo", booking_tokens=tokens))
    return result


def _pair(opt: TripOption) -> tuple[date, date]:
    return opt.outbound.departure.date(), opt.inbound.departure.date()


# ---------------------------------------------------------------------------
# Hauptsuche
# ---------------------------------------------------------------------------

def plan_and_search(profile: TripProfile, storage, budget: int,
                    today: Optional[date] = None, unpriced_out: Optional[list] = None
                    ) -> tuple[list[TripOption], dict[tuple, PriceInsights]]:
    """Insights-Schluessel: ("out", tag), ("ret", tag) aus One-Way-Suchen und
    ("rt", hin, rueck) aus Roundtrip-Suchen (nur diese passen zum Gesamtpreis).
    unpriced_out: sammelt (richtung, Leg) fuer Fluege ohne Preis bei Google."""
    today = today or date.today()
    b = _Budget(storage, budget)
    out_days, ret_days = outbound_days(profile, today), return_days(profile, today)

    # Bei grossem Budget Reserve fuer die Verfeinerung zurueckhalten.
    reserve = REFINE_RESERVE if b.limit >= RESERVE_MIN_BUDGET else 0

    out_offers: dict[date, list[OneWayOffer]] = {}
    ret_offers: dict[date, list[OneWayOffer]] = {}
    insights: dict[tuple[str, date], PriceInsights] = {}
    for kind, day in _query_order(out_days, ret_days):
        if b.left - reserve <= 0:
            break
        if kind == "out":
            res = _oneway(storage, profile.origins, profile.destinations, day)
            if res is not None:
                out_offers[day] = res.offers
                insights[("out", day)] = res.insights
                if unpriced_out is not None:
                    unpriced_out.extend(("out", leg) for leg in res.unpriced)
        else:
            res = _oneway(storage, profile.destinations, profile.origins, day)
            if res is not None:
                ret_offers[day] = res.offers
                insights[("ret", day)] = res.insights
                if unpriced_out is not None:
                    unpriced_out.extend(("ret", leg) for leg in res.unpriced)

    min_n = profile.min_nights - profile.flex_days
    max_n = profile.max_nights + profile.flex_days
    options: list[TripOption] = []
    for od, oo in out_offers.items():
        for rd, ro in ret_offers.items():
            if min_n <= (rd - od).days <= max_n:
                options.extend(_combine(oo, ro, profile.allow_open_jaw))

    options = apply_utility(options, profile)

    # Verfeinerung der besten Tagespaare per echtem Roundtrip.
    seen: set[tuple[date, date]] = set()
    refine: list[TripOption] = []
    for opt in options:
        p = _pair(opt)
        if p not in seen:
            seen.add(p)
            refine.append(opt)
        if len(refine) >= REFINE_TOP:
            break
    extra: list[TripOption] = []
    for opt in refine:
        if b.left < REFINE_CALLS:
            break
        od, rd = _pair(opt)
        try:
            rt_ins: list[PriceInsights] = []
            extra.extend(search_roundtrip([opt.outbound.origin], [opt.outbound.destination],
                                          od, rd, storage, max_outbound=1, insights_out=rt_ins))
            if rt_ins:
                insights[("rt", od, rd)] = rt_ins[0]
        except MissingApiKeyError:
            raise
        except (NoFlightsError, UpstreamError) as exc:
            logger.warning("Roundtrip %s/%s uebersprungen: %s", od, rd, type(exc).__name__)
    if extra:
        options = apply_utility(options + extra, profile)
    return options, insights


# ---------------------------------------------------------------------------
# Tracking
# ---------------------------------------------------------------------------

def _parse_key(key: str) -> tuple[tuple[str, str, date], tuple[str, str, date]]:
    out_s, ret_s = key.split("_", 1)
    parsed = []
    for s in (out_s, ret_s):
        o, d, ds = s.split("-", 2)
        parsed.append((o, d, date.fromisoformat(ds)))
    return parsed[0], parsed[1]


def track_with_insights(profile: TripProfile, storage, keys: list[str], budget: int,
                        today: Optional[date] = None, unpriced_out: Optional[list] = None
                        ) -> tuple[list[TripOption], dict[tuple, PriceInsights]]:
    """Wie plan_and_search, aber nur fuer die Tage der Keys. Der erste Key (die bisher
    beste Option) wird zusaetzlich per Roundtrip abgefragt (2 Calls), damit Googles
    Preisniveau und Verlauf fuer genau diese Verbindung aktuell bleiben."""
    today = today or date.today()
    b = _Budget(storage, budget)
    wanted: set[str] = set()
    out_airports: dict[date, tuple[set, set]] = {}
    ret_airports: dict[date, tuple[set, set]] = {}
    for key in dict.fromkeys(keys):
        try:
            (oo, od, odate), (ro, rd, rdate) = _parse_key(key)
        except ValueError:
            logger.warning("Ungueltiger Key uebersprungen: %s", key)
            continue
        wanted.add(key)
        for store, a, c, day in ((out_airports, oo, od, odate), (ret_airports, ro, rd, rdate)):
            s = store.setdefault(day, (set(), set()))
            s[0].add(a)
            s[1].add(c)

    out_offers: dict[date, list[OneWayOffer]] = {}
    ret_offers: dict[date, list[OneWayOffer]] = {}
    insights: dict[tuple[str, date], PriceInsights] = {}
    queries = [("out", d, v) for d, v in sorted(out_airports.items())] + \
              [("ret", d, v) for d, v in sorted(ret_airports.items())]
    for kind, day, (origs, dests) in queries:
        if day < today:
            continue
        if b.left <= 0:
            break
        res = _oneway(storage, sorted(origs), sorted(dests), day)
        if res is None:
            continue
        insights[(kind, day)] = res.insights
        if unpriced_out is not None:
            unpriced_out.extend((kind, leg) for leg in res.unpriced)
        if kind == "out":
            out_offers[day] = res.offers
        else:
            ret_offers[day] = res.offers

    best: dict[str, TripOption] = {}
    for oo in out_offers.values():
        for ro in ret_offers.values():
            for opt in _combine(oo, ro, profile.allow_open_jaw, top=False):
                if opt.key in wanted and (opt.key not in best or opt.price < best[opt.key].price):
                    best[opt.key] = opt

    first = next((k for k in keys if k in wanted), None)
    if first and b.left >= REFINE_CALLS:
        (oo, od, odate), (_ro, _rd, rdate) = _parse_key(first)
        if odate >= today:
            try:
                rt_ins: list[PriceInsights] = []
                for opt in search_roundtrip([oo], [od], odate, rdate, storage, max_outbound=1,
                                            insights_out=rt_ins):
                    if opt.key in wanted and (opt.key not in best or opt.price < best[opt.key].price):
                        best[opt.key] = opt
                if rt_ins:
                    insights[("rt", odate, rdate)] = rt_ins[0]
            except MissingApiKeyError:
                raise
            except (NoFlightsError, UpstreamError) as exc:
                logger.warning("Roundtrip %s/%s uebersprungen: %s", odate, rdate, type(exc).__name__)
    return apply_utility(list(best.values()), profile), insights


def track(profile: TripProfile, storage, keys: list[str], budget: int,
          today: Optional[date] = None) -> list[TripOption]:
    return track_with_insights(profile, storage, keys, budget, today)[0]
