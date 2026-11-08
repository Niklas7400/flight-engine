from datetime import datetime, timedelta, timezone

from services.models import Leg, TripOption
from services.storage import MemoryStorage, get_storage
from services.storage.supabase_store import SupabaseStorage

UTC = timezone.utc


class Clock:
    def __init__(self, start):
        self.t = start

    def __call__(self):
        return self.t


def make_option(price, out_day=10, ret_day=20, dest="SID", airlines=("LH",)):
    out = Leg("FRA", dest, datetime(2027, 3, out_day, 8), datetime(2027, 3, out_day, 14),
              360, 1, list(airlines), ["FRA", "LIS", dest])
    ret = Leg(dest, "FRA", datetime(2027, 3, ret_day, 9), datetime(2027, 3, ret_day, 15),
              360, 0, ["TP"], [dest, "FRA"])
    return TripOption(out, ret, price, "roundtrip")


def test_get_storage_memory_without_env(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    assert isinstance(get_storage(), MemoryStorage)


def test_upsert_and_get_trip():
    s = MemoryStorage()
    assert s.get_trip("x") is None
    tid = s.upsert_trip("x", {"a": 1})
    assert s.upsert_trip("x", {"a": 2}) == tid
    trip = s.get_trip("x")
    assert trip["id"] == tid and trip["profile"] == {"a": 2}


def test_price_series_sorted_per_option():
    s = MemoryStorage()
    tid = s.upsert_trip("x", {})
    a, b = make_option(500), make_option(450, out_day=11)
    t1, t2 = datetime(2026, 11, 1, 6, tzinfo=UTC), datetime(2026, 11, 2, 6, tzinfo=UTC)
    s.add_observations(tid, [make_option(480)], t2)
    s.add_observations(tid, [a, b], t1)
    series = s.get_price_series(tid, a.key)
    assert [(p.observed_at, p.price) for p in series] == [(t1, 500), (t2, 480)]
    assert [p.price for p in s.get_price_series(tid, b.key)] == [450]
    assert s.get_price_series(tid, "none") == []
    assert s.get_price_series("other", a.key) == []


def test_best_price_series_daily_minimum():
    s = MemoryStorage()
    tid = s.upsert_trip("x", {})
    d1 = datetime(2026, 11, 1, 6, tzinfo=UTC)
    d1b = datetime(2026, 11, 1, 18, tzinfo=UTC)
    d2 = datetime(2026, 11, 2, 6, tzinfo=UTC)
    s.add_observations(tid, [make_option(500), make_option(430, out_day=11)], d1)
    s.add_observations(tid, [make_option(450)], d1b)
    s.add_observations(tid, [make_option(600), make_option(610, out_day=11)], d2)
    series = s.get_best_price_series(tid)
    assert [p.price for p in series] == [430, 600]
    assert series[0].observed_at.date() < series[1].observed_at.date()
    assert s.get_best_price_series("other") == []


def test_observation_fields_stored():
    s = MemoryStorage()
    tid = s.upsert_trip("x", {})
    o = make_option(500)
    s.add_observations(tid, [o], datetime(2026, 11, 1, tzinfo=UTC))
    row = s._observations[0]
    assert row["option_key"] == o.key
    assert row["out_date"].isoformat() == "2027-03-10"
    assert (row["out_origin"], row["out_destination"]) == ("FRA", "SID")
    assert (row["ret_origin"], row["ret_destination"]) == ("SID", "FRA")
    assert row["airlines"] == ["LH", "TP"]
    assert (row["out_stops"], row["ret_stops"]) == (1, 0)
    assert row["source"] == "roundtrip"


def test_api_calls_this_month_calendar_month_utc():
    clock = Clock(datetime(2026, 10, 31, 23, 59, tzinfo=UTC))
    s = MemoryStorage(now=clock)
    s.record_api_call("serpapi", "oneway")
    clock.t = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)
    s.record_api_call("serpapi", "oneway")
    clock.t = datetime(2026, 11, 20, tzinfo=UTC)
    s.record_api_call("serpapi", "roundtrip")
    s.record_api_call("other", "x")
    assert s.api_calls_this_month("serpapi") == 2
    assert s.api_calls_this_month("other") == 1
    clock.t = datetime(2026, 12, 1, tzinfo=UTC)
    assert s.api_calls_this_month("serpapi") == 0


def test_cache_ttl_12h():
    clock = Clock(datetime(2026, 11, 8, 12, tzinfo=UTC))
    s = MemoryStorage(now=clock)
    assert s.cache_get("k") is None
    s.cache_set("k", {"v": 1})
    clock.t += timedelta(hours=11, minutes=59)
    assert s.cache_get("k") == {"v": 1}
    clock.t += timedelta(minutes=2)
    assert s.cache_get("k") is None
    s.cache_set("k", {"v": 2})
    assert s.cache_get("k") == {"v": 2}


def test_latest_recommendation():
    clock = Clock(datetime(2026, 11, 8, tzinfo=UTC))
    s = MemoryStorage(now=clock)
    tid = s.upsert_trip("x", {})
    assert s.latest_recommendation(tid) is None
    s.save_recommendation(tid, {"n": 1})
    s.save_recommendation(tid, {"n": 2})  # gleicher Zeitstempel -> zuletzt gespeichert
    clock.t += timedelta(hours=1)
    s.save_recommendation("other", {"n": 9})
    assert s.latest_recommendation(tid) == {"n": 2}


# --- SupabaseStorage mit Fake-Client ------------------------------------

class FakeResp:
    def __init__(self, data=None, count=None):
        self.data, self.count = data, count


class FakeQuery:
    def __init__(self, db, table):
        self.db, self.table, self.calls = db, table, []

    def __getattr__(self, name):
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self
        return method

    def execute(self):
        self.db.queries.append(self)
        return self.db.responses.get(self.table, FakeResp([]))


class FakeClient:
    def __init__(self, responses):
        self.responses, self.queries = responses, []

    def table(self, name):
        return FakeQuery(self, name)


def test_supabase_best_series_and_calls():
    rows = [
        {"observed_at": "2026-11-01T06:00:00+00:00", "price": "500.00"},
        {"observed_at": "2026-11-01T18:00:00Z", "price": "430.50"},
        {"observed_at": "2026-11-02T06:00:00+00:00", "price": "600"},
    ]
    client = FakeClient({"price_observations": FakeResp(rows), "api_usage": FakeResp([], count=7)})
    s = SupabaseStorage(client=client, now=lambda: datetime(2026, 11, 8, tzinfo=UTC))
    assert [p.price for p in s.get_best_price_series("tid")] == [430.5, 600.0]
    assert s.api_calls_this_month("serpapi") == 7
    calls = dict((c[0], c[1]) for c in client.queries[-1].calls)
    assert calls["gte"] == ("called_at", "2026-11-01T00:00:00+00:00")
    assert calls["lt"] == ("called_at", "2026-12-01T00:00:00+00:00")


def test_supabase_upsert_trip_and_cache_ttl():
    client = FakeClient({
        "trips": FakeResp([{"id": "abc"}]),
        "search_cache": FakeResp([{"value": {"v": 1}, "fetched_at": "2026-11-08T00:00:00+00:00"}]),
    })
    now = datetime(2026, 11, 8, 6, tzinfo=UTC)
    s = SupabaseStorage(client=client, now=lambda: now)
    assert s.upsert_trip("x", {"a": 1}) == "abc"
    assert s.cache_get("k") == {"v": 1}
    s2 = SupabaseStorage(client=client, now=lambda: now + timedelta(hours=7))
    assert s2.cache_get("k") is None


def test_list_trips_sorted_and_tracking_only():
    s = MemoryStorage()
    s.upsert_trip("b", {"n": 1})
    s.upsert_trip("a", {"n": 2})
    s._trips["b"]["tracking_enabled"] = False
    assert [t["name"] for t in s.list_trips()] == ["a", "b"]
    assert [t["name"] for t in s.list_trips(tracking_only=True)] == ["a"]
    assert set(s.list_trips()[0]) == {"id", "name", "profile", "tracking_enabled"}


def test_upsert_keeps_tracking_enabled():
    s = MemoryStorage()
    assert s.list_trips()[:1] == []
    s.upsert_trip("x", {"n": 1})
    assert s.get_trip("x")["tracking_enabled"] is True
    s._trips["x"]["tracking_enabled"] = False
    s.upsert_trip("x", {"n": 2})
    assert s.list_trips()[0]["tracking_enabled"] is False
    assert s.list_trips()[0]["profile"] == {"n": 2}


def test_supabase_list_trips_filters_and_orders():
    rows = [{"id": "1", "name": "a", "profile": {}, "tracking_enabled": True}]
    client = FakeClient({"trips": FakeResp(rows)})
    s = SupabaseStorage(client=client)
    assert s.list_trips(tracking_only=True) == rows
    calls = [c[0] for c in client.queries[-1].calls]
    assert "eq" in calls and "order" in calls
