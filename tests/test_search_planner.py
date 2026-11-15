from datetime import date, datetime, timedelta, timezone

import pytest

import services.search_planner as sp
from services.flight_api import MissingApiKeyError
from services.storage.memory import MemoryStorage
from services.trip_profile import load_profile
from tests.helpers_flights import Clock, FakeApi, fake_utility, leg
from services.models import OneWayOffer, TripOption

TODAY = date(2026, 11, 8)


@pytest.fixture
def profile():
    return load_profile("trips/kapverden_2027.yaml")


@pytest.fixture
def setup(monkeypatch):
    storage = MemoryStorage()
    api = FakeApi(storage)
    api.patch(monkeypatch, sp)
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
    api.patch(monkeypatch, sp)
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


def _key(od, rd, o="FRA", d="SID"):
    return TripOption(leg(o, d, od), leg(d, o, rd), 1, "x").key


def _clock_setup(monkeypatch, start=None):
    clock = Clock(start or datetime(2026, 11, 8, 12, tzinfo=timezone.utc))
    storage = MemoryStorage(now=clock)
    api = FakeApi(storage)
    api.patch(monkeypatch, sp)
    return clock, storage, api


def test_track_with_keys(profile, setup):
    storage, api = setup
    key = _key(date(2027, 3, 20), date(2027, 3, 30))
    key2 = _key(date(2027, 3, 20), date(2027, 4, 1))
    res = sp.track(profile, storage, [key, key2, key, "kaputt"], 20, TODAY)
    # alle Optionen des Fensters (nicht nur die Keys), volle Flughaefen des Profils
    assert {key, key2} <= {o.key for o in res} and len(res) > 2
    assert all(o[0] in (("FRA",), ("BVC", "RAI", "SID")) or len(o[0]) == 3 for o in api.oneway_days)
    assert ("FRA",) in {o[0] for o in api.oneway_days}
    # bekannt: 1 Hin- + 2 Rueckflugtage (<= ceil(20/2)); Erkundung bis auf 2 Calls Reserve
    first_days = [d for _, d in api.oneway_days[:3]]
    assert first_days[0] == date(2027, 3, 20) and set(first_days) == {
        date(2027, 3, 20), date(2027, 3, 30), date(2027, 4, 1)}
    assert len(api.oneway_days) == 18
    # Roundtrip-Abfrage der besten Option
    assert len(api.roundtrips) == 1
    assert storage.api_calls_this_month("serpapi") == 18 + 2


def test_track_budget(profile, setup):
    storage, _ = setup
    key = _key(date(2027, 3, 20), date(2027, 3, 30))
    sp.track(profile, storage, [key], 1, TODAY)
    assert storage.api_calls_this_month("serpapi") == 1


def test_track_ignores_keys_outside_window_and_past(profile, setup):
    storage, api = setup
    keys = [_key(date(2027, 1, 5), date(2027, 1, 15)), _key(date(2026, 11, 1), date(2027, 3, 30))]
    sp.track(profile, storage, keys, 4, TODAY)
    # nur der Rueckflugtag 30.3. liegt im Fenster und ist bekannt -> zuerst abgefragt
    assert api.oneway_days[0] == (("SID", "BVC", "RAI"), date(2027, 3, 30))


def test_track_discovers_cheaper_unknown_day_via_exploration(profile, monkeypatch):
    clock, storage, api = _clock_setup(monkeypatch)
    api.delta = {date(2027, 3, 18): -90, date(2027, 3, 26): -90}   # frueheste Tage, nicht in den Keys
    key = _key(date(2027, 3, 20), date(2027, 3, 30))
    res = sp.track(profile, storage, [key], 12, TODAY)
    best = res[0]
    assert (best.outbound.departure.date(), best.inbound.departure.date()) == (
        date(2027, 3, 18), date(2027, 3, 26))
    assert best.key != key and best.stale_days == 0


def test_track_discovers_cheaper_day_from_cache(profile, monkeypatch):
    clock, storage, api = _clock_setup(monkeypatch)
    api.delta = {date(2027, 4, 2): -90, date(2027, 4, 11): -90}
    sp.plan_and_search(profile, storage, 100, TODAY)         # frueherer Lauf (volle Suche)
    clock.t += timedelta(days=2)                              # Cache laenger als 12h alt
    api.oneway_days.clear()
    key = _key(date(2027, 3, 20), date(2027, 3, 30))
    res = sp.track(profile, storage, [key], 1, TODAY, now=clock.t)
    assert len(api.oneway_days) == 1                          # nur der bekannte Hinflugtag
    best = res[0]
    assert (best.outbound.departure.date(), best.inbound.departure.date()) == (
        date(2027, 4, 2), date(2027, 4, 11))
    assert best.stale_days == 2


@pytest.mark.parametrize("budget", [0, 1, 2, 5, 6, 9, 20, 60])
def test_track_budget_never_exceeded(profile, setup, budget):
    storage, _ = setup
    keys = [_key(date(2027, 3, 20 + i), date(2027, 3, 30 + i % 2)) for i in range(5)]
    sp.track(profile, storage, keys, budget, TODAY)
    assert storage.api_calls_this_month("serpapi") <= budget


def test_track_budget_9_split(profile, setup):
    storage, api = setup
    keys = [_key(date(2027, 3, 20 + i), date(2027, 3, 30) + timedelta(days=i)) for i in range(5)]
    sp.track(profile, storage, keys, 9, TODAY)
    # 5 bekannte Tage (ceil(9/2)) + 2 Erkundung + 2 Roundtrip (Reserve)
    known = {date(2027, 3, 20 + i) for i in range(5)} | {date(2027, 3, 30) + timedelta(days=i) for i in range(5)}
    assert all(d in known for _, d in api.oneway_days[:5])
    assert len(api.oneway_days) == 7 and len(api.roundtrips) == 1
    assert storage.api_calls_this_month("serpapi") == 9


def test_track_staleness_rotation_covers_window(profile, monkeypatch):
    clock, storage, api = _clock_setup(monkeypatch)
    key = _key(date(2027, 3, 20), date(2027, 3, 30))
    window = {("out", d) for d in sp.outbound_days(profile, TODAY)} | \
             {("ret", d) for d in sp.return_days(profile, TODAY)}
    seen, runs = set(), 0
    while seen != window and runs < 30:
        runs += 1
        clock.t += timedelta(days=1)
        api.oneway_days.clear()
        sp.track(profile, storage, [key], 9, TODAY, now=clock.t)
        seen |= {("out" if o == ("FRA",) else "ret", d) for o, d in api.oneway_days}
    assert seen == window
    assert runs <= 12   # 44 Tage; je Lauf 2 bekannt + 5 Erkundung + 2 Auffrischung der besten Option


def test_stale_best_is_refreshed(profile, monkeypatch):
    clock, storage, api = _clock_setup(monkeypatch)
    api.delta = {date(2027, 4, 2): -90, date(2027, 4, 11): -90}
    sp.plan_and_search(profile, storage, 100, TODAY)
    clock.t += timedelta(days=2)
    api.oneway_days.clear()
    key = _key(date(2027, 3, 20), date(2027, 3, 30))
    # 6 = 2 bekannt + 2 Erkundung (Mar 18/26) + 2 fuer die veraltete beste Option
    res = sp.track(profile, storage, [key], 6, TODAY, now=clock.t)
    queried = {d for _, d in api.oneway_days}
    assert {date(2027, 4, 2), date(2027, 4, 11)} <= queried
    assert res[0].stale_days == 0
    assert (res[0].outbound.departure.date(), res[0].inbound.departure.date()) == (
        date(2027, 4, 2), date(2027, 4, 11))
    assert storage.api_calls_this_month("serpapi") - 60 <= 6


def test_cache_older_than_21_days_ignored(profile, monkeypatch):
    clock, storage, api = _clock_setup(monkeypatch)
    sp.plan_and_search(profile, storage, 100, TODAY)
    clock.t += timedelta(days=22)
    assert sp.track(profile, storage, [], 0, TODAY, now=clock.t) == []
    clock.t -= timedelta(days=2)    # 20 Tage alt -> nutzbar
    res = sp.track(profile, storage, [], 0, TODAY, now=clock.t)
    assert res and all(o.stale_days == 20 for o in res)
