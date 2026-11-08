from datetime import date

import pytest

import services.search_planner as sp
from services.flight_api import MissingApiKeyError
from services.storage.memory import MemoryStorage
from services.trip_profile import load_profile
from tests.helpers_flights import FakeApi, fake_utility, leg
from services.models import OneWayOffer, TripOption

TODAY = date(2026, 11, 8)


@pytest.fixture
def profile():
    return load_profile("trips/kapverden_2027.yaml")


@pytest.fixture
def setup(monkeypatch):
    storage = MemoryStorage()
    api = FakeApi(storage)
    monkeypatch.setattr(sp, "search_oneway", api.oneway)
    monkeypatch.setattr(sp, "search_roundtrip", api.roundtrip)
    monkeypatch.setattr(sp, "apply_utility", fake_utility)
    return storage, api


def test_days(profile):
    out = sp.outbound_days(profile, TODAY)
    ret = sp.return_days(profile, TODAY)
    assert out[0] == date(2027, 3, 18) and out[-1] == date(2027, 4, 8) and len(out) == 22
    assert ret[0] == date(2027, 3, 26) and ret[-1] == date(2027, 4, 16) and len(ret) == 22


def test_days_not_before_today(profile):
    assert sp.outbound_days(profile, date(2027, 3, 25))[0] == date(2027, 3, 25)


def test_full_search_call_count(profile, setup):
    storage, api = setup
    options, insights = sp.plan_and_search(profile, storage, 100, TODAY)
    assert len(api.oneway_days) == 44
    assert len(api.roundtrips) == 8
    assert storage.api_calls_this_month("serpapi") == 44 + 16
    assert options and set(insights) == (
        {("out", d) for d in sp.outbound_days(profile, TODAY)}
        | {("ret", d) for d in sp.return_days(profile, TODAY)})
    assert any(o.source == "roundtrip" for o in options)
    assert options[0].effective_cost == min(o.effective_cost for o in options)


@pytest.mark.parametrize("budget", [0, 1, 5, 11, 30, 43])
def test_budget_never_exceeded(profile, setup, budget):
    storage, _ = setup
    sp.plan_and_search(profile, storage, budget, TODAY)
    assert storage.api_calls_this_month("serpapi") <= budget


def test_tight_budget_spreads_window(profile, setup):
    storage, api = setup
    sp.plan_and_search(profile, storage, 10, TODAY)
    out_days = sorted(d for o, d in api.oneway_days if o == ("FRA",))
    assert len(out_days) == 5
    gaps = [(b - a).days for a, b in zip(out_days, out_days[1:])]
    assert all(g == 2 for g in gaps)


def test_skips_failing_days(profile, monkeypatch):
    storage = MemoryStorage()
    api = FakeApi(storage, fail_days=[date(2027, 3, 20)])
    monkeypatch.setattr(sp, "search_oneway", api.oneway)
    monkeypatch.setattr(sp, "search_roundtrip", api.roundtrip)
    monkeypatch.setattr(sp, "apply_utility", fake_utility)
    options, insights = sp.plan_and_search(profile, storage, 100, TODAY)
    assert ("out", date(2027, 3, 20)) not in insights
    assert options


def test_missing_key_propagates(profile, monkeypatch):
    def boom(*a, **k):
        raise MissingApiKeyError("x")
    monkeypatch.setattr(sp, "search_oneway", boom)
    with pytest.raises(MissingApiKeyError):
        sp.plan_and_search(profile, MemoryStorage(), 10, TODAY)


def _offer(o, d, day, hour, price):
    return OneWayOffer(leg(o, d, day, hour), price, f"t{o}{d}{hour}")


def test_open_jaw_filter_and_arrival_order(profile):
    out = [_offer("FRA", "SID", date(2027, 3, 20), 8, 100)]
    ret = [_offer("BVC", "FRA", date(2027, 3, 30), 8, 100),
           _offer("SID", "FRA", date(2027, 3, 30), 9, 120),
           _offer("SID", "FRA", date(2027, 3, 20), 6, 50)]  # Abflug vor Ankunft (14:00)
    res = sp._combine(out, ret, allow_open_jaw=False)
    assert [(r.inbound.origin, r.price) for r in res] == [("SID", 220)]
    assert res[0].source == "oneway_combo" and len(res[0].booking_tokens) == 2
    res = sp._combine(out, ret, allow_open_jaw=True)
    assert {r.inbound.origin for r in res} == {"BVC", "SID"} and len(res) == 2


def test_combine_limits_top5():
    out = [_offer("FRA", "SID", date(2027, 3, 20), 8, 100 + i) for i in range(9)]
    ret = [_offer("SID", "FRA", date(2027, 3, 30), 8, 100 + i) for i in range(9)]
    assert len(sp._combine(out, ret, False)) == 25


def test_track_with_keys(profile, setup):
    storage, api = setup
    key = TripOption(leg("FRA", "SID", date(2027, 3, 20)), leg("SID", "FRA", date(2027, 3, 30)),
                     1, "x").key
    key2 = TripOption(leg("FRA", "SID", date(2027, 3, 20)), leg("SID", "FRA", date(2027, 4, 1)),
                      1, "x").key
    res = sp.track(profile, storage, [key, key2, key], 20, TODAY)
    assert sorted(o.key for o in res) == sorted([key, key2])
    # dedupliziert: 1 Hinflugtag + 2 Rueckflugtage
    assert len(api.oneway_days) == 3
    assert storage.api_calls_this_month("serpapi") == 3


def test_track_budget(profile, setup):
    storage, _ = setup
    key = TripOption(leg("FRA", "SID", date(2027, 3, 20)), leg("SID", "FRA", date(2027, 3, 30)),
                     1, "x").key
    sp.track(profile, storage, [key], 1, TODAY)
    assert storage.api_calls_this_month("serpapi") == 1
