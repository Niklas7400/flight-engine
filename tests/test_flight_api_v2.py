import json
from datetime import date, datetime

import pytest

from services import flight_api
from services.flight_api import (NoFlightsError, UpstreamError, MissingApiKeyError,
                                 search_oneway, search_roundtrip)

D = date(2099, 7, 10)
R = date(2099, 7, 20)


class Store:
    def __init__(self):
        self.cache, self.calls = {}, []

    def cache_get(self, key):
        return self.cache.get(key)

    def cache_set(self, key, value):
        self.cache[key] = value

    def record_api_call(self, provider, kind):
        self.calls.append((provider, kind))


class Search:
    handler = None
    calls = []

    def __init__(self, params):
        Search.calls.append(dict(params))
        self.params = params

    def get_dict(self):
        r = Search.handler(self.params)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture(autouse=True)
def fake(monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "k")
    monkeypatch.setattr(flight_api, "GoogleSearch", Search)
    Search.calls = []
    Search.handler = lambda p: {}


def seg(dep, arr, t1, t2, airline="LH", dur=90):
    return {"airline": airline, "duration": dur,
            "departure_airport": {"id": dep, "time": t1},
            "arrival_airport": {"id": arr, "time": t2}}


def flight(price, segs, **kw):
    return {"price": price, "flights": segs, **kw}


def test_oneway_parsing_and_insights():
    Search.handler = lambda p: {
        "best_flights": [flight(300, [seg("FRA", "LIS", "2099-07-10 08:00", "2099-07-10 10:00", "LH", 180),
                                      seg("LIS", "SID", "2099-07-10 12:00", "2099-07-10 15:30", "TP", 210)],
                                layovers=[{"duration": 120}], booking_token="bt")],
        "other_flights": [flight("$250", [seg("MUC", "SID", "2099-07-10 06:00", "2099-07-10 14:00", "TP", 500)],
                                 total_duration=480), flight("x", [seg("A", "B", "2099-07-10 06:00", "2099-07-10 07:00")])],
        "price_insights": {"lowest_price": 250, "price_level": "low",
                           "typical_price_range": [280, 400], "price_history": [[1700000000, 260], [1700086400, 270.5]]},
    }
    st = Store()
    res = search_oneway(["FRA", "MUC"], ["SID"], D, st)
    assert [o.price for o in res.offers] == [250.0, 300.0]
    leg = res.offers[1].leg
    assert leg.departure == datetime(2099, 7, 10, 8, 0) and leg.stops == 1
    assert leg.duration_min == 180 + 210 + 120
    assert leg.airlines == ["LH", "TP"] and leg.route == ["FRA", "LIS", "SID"]
    assert (leg.origin, leg.destination) == ("FRA", "SID")
    assert res.offers[1].booking_token == "bt"
    assert res.offers[0].leg.duration_min == 480
    i = res.insights
    assert (i.lowest_price, i.price_level, i.typical_low, i.typical_high) == (250.0, "low", 280.0, 400.0)
    assert i.history == [(1700000000, 260.0), (1700086400, 270.5)]
    p = Search.calls[0]
    assert p["departure_id"] == "FRA,MUC" and p["type"] == 2 and p["gl"] == "de" and p["adults"] == 1
    assert p["currency"] == "EUR" and p["hl"] == "en" and p["engine"] == "google_flights"


def test_cache_prevents_second_call_and_counts():
    Search.handler = lambda p: {"best_flights": [flight(100, [seg("FRA", "SID", "2099-07-10 08:00", "2099-07-10 12:00")])]}
    st = Store()
    a = search_oneway(["FRA"], ["SID"], D, st)
    b = search_oneway(["FRA"], ["SID"], D, st)
    assert not a.from_cache and b.from_cache
    assert len(Search.calls) == 1 and st.calls == [("serpapi", "oneway")]
    assert all("api_key" not in k for k in st.cache)
    assert json.loads(next(iter(st.cache)))["outbound_date"] == "2099-07-10"
    assert b.offers[0].price == 100.0


def test_roundtrip_with_departure_token():
    def h(p):
        if "departure_token" not in p:
            return {"best_flights": [
                flight(900, [seg("FRA", "SID", "2099-07-10 08:00", "2099-07-10 14:00")], departure_token="t900"),
                flight(500, [seg("FRA", "SID", "2099-07-10 09:00", "2099-07-10 15:00")], departure_token="t500"),
                flight(600, [seg("MUC", "SID", "2099-07-10 07:00", "2099-07-10 13:00")], departure_token="t600"),
                flight(700, [seg("MUC", "SID", "2099-07-10 07:30", "2099-07-10 13:30")], departure_token="t700"),
            ]}
        base = {"t500": 520, "t600": 650}[p["departure_token"]]
        return {"best_flights": [flight(base + 50, [seg("SID", "FRA", "2099-07-20 10:00", "2099-07-20 16:00")]),
                                 flight(base, [seg("SID", "LIS", "2099-07-20 09:00", "2099-07-20 12:00", "TP"),
                                               seg("LIS", "FRA", "2099-07-20 13:00", "2099-07-20 17:00", "TP")],
                                        booking_token="rb")]}
    Search.handler = h
    st = Store()
    opts = search_roundtrip(["FRA", "MUC"], ["SID"], D, R, st, max_outbound=2)
    assert len(Search.calls) == 3
    assert st.calls == [("serpapi", "roundtrip"), ("serpapi", "return_leg"), ("serpapi", "return_leg")]
    assert [o.price for o in opts] == [520.0, 650.0]
    o = opts[0]
    assert o.source == "roundtrip" and o.outbound.origin == "FRA" and o.inbound.stops == 1
    assert o.inbound.route == ["SID", "LIS", "FRA"] and "rb" in o.booking_tokens
    assert Search.calls[0]["return_date"] == "2099-07-20" and Search.calls[0]["type"] == 1
    assert Search.calls[1]["departure_token"] == "t500"
    # Zweiter Aufruf komplett aus Cache
    search_roundtrip(["FRA", "MUC"], ["SID"], D, R, st, max_outbound=2)
    assert len(Search.calls) == 3 and len(st.calls) == 3


def test_roundtrip_price_falls_back_to_first_call():
    def h(p):
        if "departure_token" not in p:
            return {"best_flights": [flight(480, [seg("FRA", "SID", "2099-07-10 08:00", "2099-07-10 14:00")], departure_token="t")]}
        return {"best_flights": [{"flights": [seg("SID", "FRA", "2099-07-20 10:00", "2099-07-20 16:00")]}]}
    Search.handler = h
    opts = search_roundtrip(["FRA"], ["SID"], D, R, Store(), max_outbound=1)
    assert [o.price for o in opts] == [480.0]


def test_roundtrip_skips_outbound_without_return():
    def h(p):
        if "departure_token" not in p:
            return {"best_flights": [flight(480, [seg("FRA", "SID", "2099-07-10 08:00", "2099-07-10 14:00")], departure_token="t")]}
        return {"error": "Google Flights hasn't returned any results for this query."}
    Search.handler = h
    assert search_roundtrip(["FRA"], ["SID"], D, R, Store()) == []


def test_errors():
    st = Store()
    Search.handler = lambda p: RuntimeError("https://x?api_key=k")
    with pytest.raises(UpstreamError) as e:
        search_oneway(["FRA"], ["SID"], D, st)
    assert "k" not in str(e.value) and st.calls == [] and st.cache == {}
    Search.handler = lambda p: {"error": "Google Flights hasn't returned any results"}
    with pytest.raises(NoFlightsError):
        search_oneway(["FRA"], ["SID"], D, st)
    Search.handler = lambda p: {"error": "Invalid API key"}
    with pytest.raises(UpstreamError):
        search_oneway(["FRA"], ["SID"], D, st)
    Search.handler = lambda p: ["nope"]
    with pytest.raises(UpstreamError):
        search_oneway(["FRA"], ["SID"], D, st)
    assert st.cache == {}


def test_missing_key(monkeypatch):
    monkeypatch.delenv("SERPAPI_KEY")
    monkeypatch.setattr(flight_api, "load_dotenv", lambda *a, **k: None, raising=False)
    with pytest.raises(MissingApiKeyError):
        search_oneway(["FRA"], ["SID"], D, Store())
    assert Search.calls == []
