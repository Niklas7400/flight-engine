"""Fakes fuer search_oneway/search_roundtrip (keine Netzwerk-Calls)."""
from datetime import date, datetime, timedelta

from services.flight_api import NoFlightsError
from services.models import Leg, OneWayOffer, PriceInsights, SearchResult, TripOption


def leg(o, d, day, hour=10, dur=360):
    dep = datetime(day.year, day.month, day.day, hour)
    return Leg(o, d, dep, dep + timedelta(minutes=dur), dur, 0, ["X"], [o, d])


class FakeApi:
    """Zaehlt echte Calls ueber storage.record_api_call; Preis = base + Tag-Offset."""

    def __init__(self, storage, fail_days=()):
        self.storage = storage
        self.fail_days = set(fail_days)
        self.oneway_days = []
        self.roundtrips = []

    def oneway(self, origins, destinations, day, storage):
        self.storage.record_api_call("serpapi", "oneway")
        self.oneway_days.append((tuple(origins), day))
        if day in self.fail_days:
            raise NoFlightsError("none")
        offers = []
        for i, d in enumerate(destinations):
            for o in origins:
                offers.append(OneWayOffer(leg(o, d, day, 10 + i), 100.0 + day.day + 10 * i,
                                          f"tok-{o}{d}{day}"))
        return SearchResult(offers, PriceInsights(typical_low=150.0))

    def roundtrip(self, origins, destinations, out_day, ret_day, storage, max_outbound=3):
        self.storage.record_api_call("serpapi", "roundtrip")
        self.storage.record_api_call("serpapi", "return_leg")
        self.roundtrips.append((out_day, ret_day))
        return [TripOption(leg(origins[0], destinations[0], out_day),
                           leg(destinations[0], origins[0], ret_day), 150.0, "roundtrip", ["rt"])]


def fake_utility(options, profile):
    for o in options:
        o.effective_cost = o.price
    return sorted(options, key=lambda o: o.effective_cost)
