"""Suchplanung: Tagesabfragen, One-Way-Kombination, Verfeinerung per Roundtrip, Tracking.

Budget = maximale Anzahl ECHTER API-Calls (Cache-Treffer zaehlen nicht). Gezaehlt wird
ueber storage.api_calls_this_month("serpapi") vor/nach den Calls.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from services.flight_api import (
    MissingApiKeyError,
    NoFlightsError,
    UpstreamError,
    cached_oneway,
    oneway_cache_key,
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

STALE_MAX_AGE_SECONDS = 21 * 86400   # aeltere Cache-Daten werden ignoriert
TRACK_RESERVE = 2                    # Roundtrip-Refresh der besten Option
TRACK_RESERVE_MIN_BUDGET = 6


def _parse_key(key: str) -> tuple[tuple[str, str, date], tuple[str, str, date]]:
    out_s, ret_s = key.split("_", 1)
    parsed = []
    for s in (out_s, ret_s):
        o, d, ds = s.split("-", 2)
        parsed.append((o, d, date.fromisoformat(ds)))
    return parsed[0], parsed[1]


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _combine_days(out_data: dict, ret_data: dict, profile: TripProfile) -> list[TripOption]:
    """out_data/ret_data: Tag -> (offers, alter_in_tagen). Wie plan_and_search kombiniert;
    stale_days = Alter der aelteren der beiden Datenquellen."""
    min_n = profile.min_nights - profile.flex_days
    max_n = profile.max_nights + profile.flex_days
    options: list[TripOption] = []
    for od, (oo, o_age) in out_data.items():
        for rd, (ro, r_age) in ret_data.items():
            if min_n <= (rd - od).days <= max_n:
                for opt in _combine(oo, ro, profile.allow_open_jaw):
                    opt.stale_days = max(o_age, r_age)
                    options.append(opt)
    return options


def _interleave(a: list, b: list) -> list:
    result = []
    for i in range(max(len(a), len(b))):
        if i < len(a):
            result.append(a[i])
        if i < len(b):
            result.append(b[i])
    return result


def track_with_insights(profile: TripProfile, storage, keys: list[str], budget: int,
                        today: Optional[date] = None, unpriced_out: Optional[list] = None,
                        now: Optional[datetime] = None
                        ) -> tuple[list[TripOption], dict[tuple, PriceInsights]]:
    """Tracking-Lauf mit Budget (echte Calls). Immer mit den vollen Flughaefen des Profils
    (gleiche Cache-Keys wie plan_and_search).
    1. bekannte Tage (aus den Keys, erster Key zuerst), hoechstens ceil(budget/2) Calls,
    2. Erkundung: aelteste Tage des Fensters (nach fetched_at), Reserve fuer den Roundtrip,
    3. uebrige Fenstertage nur aus dem Cache (max. 21 Tage alt, kein Call),
    4. ist die beste Option veraltet: deren Tage frisch abfragen,
    5. Roundtrip-Refresh der besten Option (Googles Preisniveau/Verlauf).
    Rueckgabe: alle bewerteten Optionen (stale_days > 0 = nicht in diesem Lauf abgefragt)."""
    today = today or date.today()
    now = _aware(now) if now else datetime.now(timezone.utc)
    b = _Budget(storage, budget)
    out_window, ret_window = outbound_days(profile, today), return_days(profile, today)
    out_set, ret_set = set(out_window), set(ret_window)

    # Tage der Keys (nur im Fenster), erster Key zuerst
    known_out: dict[date, None] = {}
    known_ret: dict[date, None] = {}
    first: Optional[tuple[date, date]] = None
    for key in dict.fromkeys(keys):
        try:
            (_o1, _d1, odate), (_o2, _d2, rdate) = _parse_key(key)
        except ValueError:
            logger.warning("Ungueltiger Key uebersprungen: %s", key)
            continue
        if first is None:
            first = (odate, rdate)
        if odate in out_set:
            known_out.setdefault(odate)
        if rdate in ret_set:
            known_ret.setdefault(rdate)
    known: list[tuple[str, date]] = []
    if first:
        if first[0] in known_out:
            known.append(("out", first[0]))
        if first[1] in known_ret:
            known.append(("ret", first[1]))
    known += [x for x in _interleave([("out", d) for d in known_out], [("ret", d) for d in known_ret])
              if x not in known]

    out_data: dict[date, tuple[list[OneWayOffer], int]] = {}
    ret_data: dict[date, tuple[list[OneWayOffer], int]] = {}
    insights: dict[tuple, PriceInsights] = {}
    fetched: set[tuple[str, date]] = set()

    def use(kind: str, day: date, res, age: int) -> None:
        (out_data if kind == "out" else ret_data)[day] = (res.offers, age)
        insights[(kind, day)] = res.insights
        if unpriced_out is not None:
            unpriced_out.extend((kind, leg) for leg in res.unpriced)

    def fetch(kind: str, day: date) -> None:
        fetched.add((kind, day))
        origs, dests = ((profile.origins, profile.destinations) if kind == "out"
                        else (profile.destinations, profile.origins))
        res = _oneway(storage, origs, dests, day)
        if res is not None:
            use(kind, day, res, 0)

    # Phase 1: bekannte Tage
    known_cap = math.ceil(b.limit / 2)
    for kind, day in known:
        if b.left <= 0 or b.used >= known_cap:
            break
        fetch(kind, day)

    # Phase 2: Erkundung (aelteste Daten zuerst)
    reserve = TRACK_RESERVE if b.limit >= TRACK_RESERVE_MIN_BUDGET else 0
    if b.left - reserve > 0:
        cand = [("out", d) for d in out_window if ("out", d) not in fetched] + \
               [("ret", d) for d in ret_window if ("ret", d) not in fetched]
        ckeys = {c: _cache_key_for(profile, c) for c in cand}
        ages = storage.cache_fetched_at_many(list(ckeys.values()))
        never = datetime.min.replace(tzinfo=timezone.utc)

        def order(kind: str) -> list[tuple[str, date]]:
            items = [c for c in cand if c[0] == kind]
            return sorted(items, key=lambda c: (_aware(ages[ckeys[c]]) if ckeys[c] in ages else never,
                                                c[1]))

        for kind, day in _interleave(order("out"), order("ret")):
            if b.left - reserve <= 0:
                break
            fetch(kind, day)

    # Phase 3: uebrige Tage aus dem Cache
    for kind, day in [("out", d) for d in out_window] + [("ret", d) for d in ret_window]:
        if (kind, day) in fetched:
            continue
        origs, dests = ((profile.origins, profile.destinations) if kind == "out"
                        else (profile.destinations, profile.origins))
        hit = cached_oneway(list(origs), list(dests), day, storage, STALE_MAX_AGE_SECONDS)
        if hit is not None:
            res, at = hit
            use(kind, day, res, max((now - _aware(at)).days, 0))

    options = apply_utility(_combine_days(out_data, ret_data, profile), profile)

    # Phase 4: beste Option veraltet -> deren Tage frisch abfragen
    if options and options[0].stale_days > 0 and b.left >= 2:
        od, rd = _pair(options[0])
        fetch("out", od)
        fetch("ret", rd)
        options = apply_utility(_combine_days(out_data, ret_data, profile), profile)

    # Phase 5: Roundtrip-Refresh der besten Option
    if options and b.left >= REFINE_CALLS:
        best = options[0]
        od, rd = _pair(best)
        try:
            rt_ins: list[PriceInsights] = []
            extra = search_roundtrip([best.outbound.origin], [best.outbound.destination], od, rd,
                                     storage, max_outbound=1, insights_out=rt_ins)
            if rt_ins:
                insights[("rt", od, rd)] = rt_ins[0]
            if extra:
                options = apply_utility(options + list(extra), profile)
        except MissingApiKeyError:
            raise
        except (NoFlightsError, UpstreamError) as exc:
            logger.warning("Roundtrip %s/%s uebersprungen: %s", od, rd, type(exc).__name__)
    return options, insights


def _cache_key_for(profile: TripProfile, cand: tuple[str, date]) -> str:
    kind, day = cand
    origs, dests = ((profile.origins, profile.destinations) if kind == "out"
                    else (profile.destinations, profile.origins))
    return oneway_cache_key(list(origs), list(dests), day)


def track(profile: TripProfile, storage, keys: list[str], budget: int,
          today: Optional[date] = None, now: Optional[datetime] = None) -> list[TripOption]:
    return track_with_insights(profile, storage, keys, budget, today, now=now)[0]
