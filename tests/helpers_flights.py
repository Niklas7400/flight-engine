"""Fakes fuer search_oneway/search_roundtrip (keine Netzwerk-Calls)."""
from datetime import date, datetime, timedelta

from services.flight_api import NoFlightsError, oneway_cache_key
from services.models import Leg, OneWayOffer, PriceInsights, SearchResult, TripOption


def leg(o, d, day, hour=10, dur=360):
    dep = datetime(day.year, day.month, day.day, hour)
    return Leg(o, d, dep, dep + timedelta(minutes=dur), dur, 0, ["X"], [o, d])


class Clock:
    """Steuerbare Uhr fuer MemoryStorage(now=...)."""

    def __init__(self, start):
        self.t = start

    def __call__(self):
        return self.t


class FakeApi:
    """Zaehlt echte Calls ueber storage.record_api_call; Preis = base + Tag-Offset."""

    def __init__(self, storage, fail_days=()):
        self.storage = storage
        self.fail_days = set(fail_days)
        self.oneway_days = []
        self.roundtrips = []
        self.delta = {}      # Tag -> Preisaufschlag (negativ = guenstiger)
        self.results = {}    # Cache-Key -> SearchResult (der Cache selbst liegt im Storage)

    def oneway(self, origins, destinations, day, storage):
        key = oneway_cache_key(origins, destinations, day)
        # wie _call_serpapi: Cache-Treffer innerhalb der TTL zaehlen nicht
        if key in self.results and storage.cache_get(key) is not None:
            return self.results[key]
        self.storage.record_api_call("serpapi", "oneway")
        self.oneway_days.append((tuple(origins), day))
        if day in self.fail_days:
            raise NoFlightsError("none")
        offers = []
        for i, d in enumerate(destinations):
            for o in origins:
                offers.append(OneWayOffer(leg(o, d, day, 10 + i),
                                          100.0 + day.day + 10 * i + self.delta.get(day, 0),
                                          f"tok-{o}{d}{day}"))
        res = SearchResult(offers, PriceInsights(typical_low=150.0))
        self.results[key] = res
        storage.cache_set(key, {"fake": True})
        return res

    def cached_oneway(self, origins, destinations, day, storage, max_age_seconds):
        key = oneway_cache_key(origins, destinations, day)
        hit = storage.cache_get_stale(key, max_age_seconds)
        if hit is None or key not in self.results:
            return None
        return self.results[key], hit[1]

    def patch(self, monkeypatch, module):
        monkeypatch.setattr(module, "search_oneway", self.oneway)
        monkeypatch.setattr(module, "cached_oneway", self.cached_oneway)
        monkeypatch.setattr(module, "search_roundtrip", self.roundtrip)
        monkeypatch.setattr(module, "apply_utility", fake_utility)

    def roundtrip(self, origins, destinations, out_day, ret_day, storage, max_outbound=3, insights_out=None):
        self.storage.record_api_call("serpapi", "roundtrip")
        self.storage.record_api_call("serpapi", "return_leg")
        self.roundtrips.append((out_day, ret_day))
        return [TripOption(leg(origins[0], destinations[0], out_day),
                           leg(destinations[0], origins[0], ret_day), 150.0, "roundtrip", ["rt"])]


def fake_utility(options, profile):
    for o in options:
        o.effective_cost = o.price
    return sorted(options, key=lambda o: o.effective_cost)
