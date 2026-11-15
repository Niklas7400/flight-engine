from datetime import date, datetime, timedelta, timezone

import pytest

import services.engine as engine
import services.search_planner as sp
from services.flight_api import NoFlightsError
from services.models import TimingAdvice
from services.storage.memory import MemoryStorage
from services.trip_profile import load_profile
from tests.helpers_flights import Clock, FakeApi, fake_utility

TODAY = date(2026, 11, 8)


@pytest.fixture
def profile():
    return load_profile("trips/kapverden_2027.yaml")


@pytest.fixture
def setup(monkeypatch):
    storage = MemoryStorage()
    api = FakeApi(storage)
    api.patch(monkeypatch, sp)
    monkeypatch.setattr(engine, "explain_choice", lambda b, c, wanted_nights=None: "erklaert")
    return storage, api


def test_run_trip_saves(profile, setup):
    storage, _ = setup
    rec = engine.run_trip(profile, storage, 100, TODAY)
    assert rec.best is not None and rec.cheapest.price <= rec.best.price
    assert len(rec.alternatives) == 5 and rec.best not in rec.alternatives
    assert rec.explanation == "erklaert"
    assert rec.api_calls_used == 60
    assert rec.api_calls_remaining_month == 250 - 60
    trip = storage.get_trip(profile.name)
    saved = storage.latest_recommendation(trip["id"])
    assert saved["best"]["key"] == rec.best.key
    assert len(storage.get_best_price_series(trip["id"])) == 1
    assert len(storage._observations) == 15
    assert rec.timing_llm is None and rec.timing_rules.action


def test_llm_timing_used_and_exceptions_caught(profile, setup):
    storage, _ = setup
    seen = {}

    def llm(best, series, insights, rules, today):
        seen["args"] = (best, series, insights, rules, today)
        return TimingAdvice(rules.action, None, None, "low", ["x"], source="llm")

    rec = engine.run_trip(profile, storage, 100, TODAY, llm_timing_fn=llm)
    assert rec.timing_llm.source == "llm" and seen["args"][4] == TODAY
    assert seen["args"][2] is not None  # Insights des Hinflugtags

    def bad(*a):
        raise RuntimeError("x")
    rec = engine.run_trip(profile, storage, 100, TODAY, llm_timing_fn=bad)
    assert rec.timing_llm is None


def test_no_options(profile, setup):
    storage, _ = setup
    with pytest.raises(NoFlightsError):
        engine.run_trip(profile, storage, 0, TODAY)


def test_track_trip_falls_back_to_run(profile, setup):
    storage, api = setup
    rec = engine.track_trip(profile, storage, 100, TODAY)
    assert len(api.oneway_days) == 44  # volle Suche


def test_track_trip_uses_keys(profile, setup):
    storage, api = setup
    first = engine.run_trip(profile, storage, 100, TODAY)
    before = storage.api_calls_this_month("serpapi")
    api.oneway_days.clear()
    rec = engine.track_trip(profile, storage, 30, TODAY)
    used = storage.api_calls_this_month("serpapi") - before
    assert 0 < used <= 30 and rec.api_calls_used == used
    assert rec.best.key in {first.best.key, first.cheapest.key, *(a.key for a in first.alternatives)}


def test_google_history_merges_with_timezone_aware_db_series():
    """Supabase liefert observed_at mit Zeitzone, Googles Verlauf ist Unix-Zeit."""
    from datetime import datetime as dt, timezone
    from services.engine import _with_google_history
    from services.models import PricePoint, PriceInsights
    own = [PricePoint(observed_at=dt(2026, 11, 8, 11, 0, tzinfo=timezone.utc), price=369.0)]
    ins = PriceInsights(history=[(1791756000, 404.0), (1794092400, 369.0)])
    merged = _with_google_history(own, ins)
    assert [p.price for p in merged][-1] == 369.0
    assert len(merged) == 3
    assert all(p.observed_at.tzinfo is None for p in merged)


def test_stale_options_not_stored_as_observations(profile, monkeypatch):
    clock = Clock(datetime.now(timezone.utc) - timedelta(days=3))
    storage = MemoryStorage(now=clock)
    api = FakeApi(storage)
    api.patch(monkeypatch, sp)
    monkeypatch.setattr(engine, "explain_choice", lambda b, c, wanted_nights=None: "x")
    engine.run_trip(profile, storage, 100, TODAY)
    n_before = len(storage._observations)
    clock.t = datetime.now(timezone.utc)
    # Budget 0: alles aus dem Cache -> alle Optionen veraltet -> keine neuen Beobachtungen
    rec = engine.track_trip(profile, storage, 0, TODAY)
    assert rec.best.stale_days >= 2
    assert len(storage._observations) == n_before
    # mit Budget wird die beste Option frisch abgefragt und beobachtet
    rec = engine.track_trip(profile, storage, 6, TODAY)
    assert rec.best.stale_days == 0
    assert len(storage._observations) > n_before
    assert all(o["price"] for o in storage._observations)
