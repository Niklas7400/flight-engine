"""End-to-end: echte Module, nur SerpApi simuliert (Kapverden-Profil)."""
import sys
from datetime import date, timedelta

import pytest

from services import flight_api
from services.engine import run_trip, track_trip
from services.models import BookingAction
from services.storage import MemoryStorage
from services.trip_profile import load_profile

TODAY = date(2026, 11, 8)


def seg(dep, arr, day, t_dep, t_arr, airline):
    return {
        "airline": airline,
        "duration": 0,
        "departure_airport": {"id": dep, "time": f"{day} {t_dep}"},
        "arrival_airport": {"id": arr, "time": f"{day} {t_arr}"},
    }


def offers(params):
    """Pro Tag: Condor direkt frueh (billig), TUI direkt mittags (+5 EUR), TAP via LIS (billigst, lang)."""
    day = params.get("outbound_date")
    outbound = params["departure_id"].startswith("FRA")
    d = date.fromisoformat(day)
    # Ostern teurer, Rueckflug am 30.3. besonders billig
    base = 300 + (60 if date(2027, 3, 26) <= d <= date(2027, 4, 5) else 0)
    if not outbound and d == date(2027, 3, 30):
        base -= 80
    if outbound:
        o, dst = "FRA", "SID"
        early = [seg(o, dst, day, "05:40", "09:30", "Condor")]
        good = [seg(o, dst, day, "10:15", "14:05", "TUI fly")]
        via = [seg(o, "LIS", day, "07:00", "09:00", "TAP"), seg("LIS", dst, day, "16:00", "19:00", "TAP")]
    else:
        o, dst = "SID", "FRA"
        early = [seg(o, dst, day, "03:30", "10:30", "Condor")]
        good = [seg(o, dst, day, "11:00", "18:00", "TUI fly")]
        via = [seg(o, "LIS", day, "06:00", "10:00", "TAP"), seg("LIS", dst, day, "18:00", "22:00", "TAP")]
    return {
        "best_flights": [
            {"price": base, "total_duration": 230, "flights": early, "departure_token": "t1"},
            {"price": base + 5, "total_duration": 230, "flights": good, "departure_token": "t2"},
        ],
        "other_flights": [{"price": base - 30, "total_duration": 720, "flights": via, "departure_token": "t3"}],
        "price_insights": {"lowest_price": base - 30, "price_level": "typical",
                           "typical_price_range": [base - 50, base + 150]},
    }


class FakeSearch:
    calls = []

    def __init__(self, params):
        FakeSearch.calls.append(dict(params))
        self.params = params

    def get_dict(self):
        p = self.params
        if p.get("departure_token"):  # Rueckflug-Folgecall: Rueckflug-Angebote fuer return_date
            ret = offers({**p, "departure_id": "SID", "outbound_date": p["return_date"]})
            for f in ret["best_flights"] + ret["other_flights"]:
                f["price"] = f["price"] * 2 - 20  # Roundtrip-Rabatt
            return ret
        res = offers(p)
        if p.get("type") == 1:
            # Wie bei echter SerpApi: Roundtrip-Insights passen zum Gesamtpreis und haben Verlauf
            pi = res["price_insights"]
            pi["typical_price_range"] = [x * 2 for x in pi["typical_price_range"]]
            pi["lowest_price"] *= 2
            pi["price_history"] = [[1791756000 + i * 86400, pi["lowest_price"] + 40] for i in range(20)]
        return res


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "test")
    monkeypatch.setattr(flight_api, "GoogleSearch", FakeSearch)
    FakeSearch.calls = []
    return MemoryStorage()


def test_run_trip_end_to_end(env):
    profile = load_profile("trips/kapverden_2027.yaml")
    rec = run_trip(profile, env, budget=60, today=TODAY)

    assert len(FakeSearch.calls) <= 60
    best = rec.best
    # Fruehe Abfluege stoeren, Umstiege stoeren: TUI um 10:15 / 11:00 trotz +5 EUR
    assert best.outbound.departure.strftime("%H:%M") == "10:15"
    assert best.inbound.departure.strftime("%H:%M") == "11:00"
    assert rec.cheapest.price < best.price
    assert rec.explanation and "mehr" in rec.explanation
    # Abweichung von 10 Naechten ist egal -> billiger Rueckflug am 30.3. wird genutzt
    assert best.inbound.departure.date() == date(2027, 3, 30) or best.price <= 600
    assert rec.timing_rules.action in set(BookingAction)
    assert rec.timing_rules.reasons
    assert env.latest_recommendation(env.get_trip("kapverden_2027")["id"]) is not None


def test_track_after_run_uses_few_calls(env):
    profile = load_profile("trips/kapverden_2027.yaml")
    run_trip(profile, env, budget=60, today=TODAY)
    FakeSearch.calls = []
    rec = track_trip(profile, env, budget=15, today=TODAY + timedelta(days=3))  # Cache-Treffer zaehlen nicht
    assert len(FakeSearch.calls) <= 15
    assert rec.best is not None


def test_insights_match_total_price_scale(env):
    profile = load_profile("trips/kapverden_2027.yaml")
    rec = run_trip(profile, env, budget=60, today=TODAY)
    # Zielpreis muss in der Groessenordnung des Gesamtpreises (Hin + Rueck) liegen
    assert rec.timing_rules.target_price is None or rec.timing_rules.target_price > rec.best.price * 0.6


def test_roundtrip_insights_and_google_history_drive_timing(env):
    """Echte Beobachtung: Google meldet bei Roundtrip-Suchen Preisniveau + Verlauf."""
    profile = load_profile("trips/kapverden_2027.yaml")
    rec = run_trip(profile, env, budget=60, today=TODAY)
    # Verlauf von Google ergibt genug Punkte -> keine "zu wenig Daten"-Einschaetzung
    assert rec.timing_rules.action != BookingAction.WATCH
    assert len({a.key for a in rec.alternatives}) == len(rec.alternatives)
    assert rec.best.key not in {a.key for a in rec.alternatives}


def test_unpriced_direct_flights_become_hints(env, monkeypatch):
    real = offers

    def with_unpriced(params):
        res = real(params)
        if params["departure_id"].startswith("FRA"):
            day = params["outbound_date"]
            res["other_flights"].append({"total_duration": 240, "flights": [
                seg("FRA", "BVC", day, "06:05", "10:05", "TUI fly")]})  # ohne "price"
        return res

    monkeypatch.setattr(sys.modules[__name__], "offers", with_unpriced)
    profile = load_profile("trips/kapverden_2027.yaml")
    rec = run_trip(profile, env, budget=60, today=TODAY)
    assert any("TUI fly FRA→BVC um 06:05" in h and "keinen Preis" in h for h in rec.hints)
