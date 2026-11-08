"""Tests fuer cli.py. Engine, LLM und Storage sind gemockt (keine Netzwerk-Calls)."""
import sys
import types
from datetime import date, datetime

import pytest

import cli
from services.flight_api import MissingApiKeyError, NoFlightsError
from services.models import (
    BookingAction, Leg, Recommendation, TimingAdvice, TripOption, TripProfile,
)
from services.trip_profile import profile_to_dict
from services.storage.memory import MemoryStorage


def _leg(origin, dest, dep, arr):
    return Leg(origin=origin, destination=dest, departure=dep, arrival=arr,
               duration_min=180, stops=0, airlines=["LH"], route=[origin, dest])


def _recommendation() -> Recommendation:
    out = _leg("FRA", "SID", datetime(2027, 3, 20, 10, 0), datetime(2027, 3, 20, 13, 0))
    back = _leg("SID", "FRA", datetime(2027, 3, 30, 9, 0), datetime(2027, 3, 30, 13, 0))
    best = TripOption(outbound=out, inbound=back, price=412.5, source="roundtrip")
    advice = TimingAdvice(action=BookingAction.WATCH, decide_by=None, target_price=None,
                          confidence="low", reasons=["Zu wenig Daten."])
    return Recommendation(trip_name="kapverden_2027", created_at=datetime(2027, 1, 1),
                          best=best, cheapest=best, alternatives=[],
                          explanation="Test-Erklaerung.", timing_rules=advice)


def _profile() -> TripProfile:
    return TripProfile(name="kapverden_2027", origins=["FRA"], destinations=["SID"],
                       earliest_departure=date(2027, 3, 19), latest_return=date(2027, 4, 15))


YAML_TEXT = """\
name: kapverden_2027
origins: [FRA]
destinations: [SID]
earliest_departure: 2027-03-19
latest_return: 2027-04-15
"""


class FakeEngine:
    """Ersetzt services.engine; merkt sich Aufrufe und wirft optional eine Exception."""

    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def run_trip(self, profile, storage, budget, today=None, llm_timing_fn=None):
        self.calls.append(("run", budget, llm_timing_fn))
        if self.error:
            raise self.error
        return _recommendation()

    def track_trip(self, profile, storage, budget, today=None, llm_timing_fn=None):
        self.calls.append(("track", budget, llm_timing_fn))
        if self.error:
            raise self.error
        return _recommendation()


@pytest.fixture
def storage(monkeypatch):
    store = MemoryStorage()
    monkeypatch.delenv("SERPAPI_MONTHLY_LIMIT", raising=False)
    monkeypatch.setattr("services.storage.get_storage", lambda: store)
    return store


@pytest.fixture
def engine(monkeypatch):
    fake = FakeEngine()
    mod = types.ModuleType("services.engine")
    mod.run_trip = fake.run_trip
    mod.track_trip = fake.track_trip
    monkeypatch.setitem(sys.modules, "services.engine", mod)
    return fake


@pytest.fixture
def trip_file(tmp_path):
    path = tmp_path / "trip.yaml"
    path.write_text(YAML_TEXT, encoding="utf-8")
    return str(path)


def _used(storage, n):
    for _ in range(n):
        storage.record_api_call("serpapi", "test")


# --- Budget-Berechnung --------------------------------------------------------

def test_effective_budget_uses_requested_when_room_is_enough():
    assert cli.effective_budget(60, used_this_month=0, limit=250) == 60


def test_effective_budget_caps_at_remaining_minus_buffer():
    # 250 - 200 - 20 = 30 < 60
    assert cli.effective_budget(60, used_this_month=200, limit=250) == 30


def test_effective_budget_below_minimum_when_month_nearly_used():
    assert cli.effective_budget(60, used_this_month=225, limit=250) == 5


def test_monthly_limit_default_and_env_override(monkeypatch):
    monkeypatch.delenv("SERPAPI_MONTHLY_LIMIT", raising=False)
    assert cli.monthly_limit() == 250
    monkeypatch.setenv("SERPAPI_MONTHLY_LIMIT", "100")
    assert cli.monthly_limit() == 100


def test_run_default_budget_is_60(storage, engine, trip_file):
    assert cli.main(["run", trip_file]) == 0
    assert engine.calls[0][0] == "run"
    assert engine.calls[0][1] == 60


def test_track_default_budget_is_15(storage, engine, trip_file):
    assert cli.main(["track", trip_file]) == 0
    assert engine.calls[0][0] == "track"
    assert engine.calls[0][1] == 15


def test_budget_is_capped_by_monthly_usage(storage, engine, trip_file, monkeypatch):
    monkeypatch.setenv("SERPAPI_MONTHLY_LIMIT", "100")
    _used(storage, 50)
    assert cli.main(["run", trip_file, "--budget", "70"]) == 0
    assert engine.calls[0][1] == 30  # min(70, 100 - 50 - 20)


def test_too_little_budget_skips_search_with_exit_0(storage, engine, trip_file, capsys):
    _used(storage, 225)  # 250 - 225 - 20 = 5 < 10
    assert cli.main(["run", trip_file]) == 0
    assert engine.calls == []
    assert "keine Suche" in capsys.readouterr().err


# --- Optionen -----------------------------------------------------------------

def test_no_llm_passes_none(storage, engine, trip_file):
    cli.main(["run", trip_file, "--no-llm"])
    assert engine.calls[0][2] is None


def test_llm_enabled_passes_llm_timing(storage, engine, trip_file):
    from services.llm_advisor import llm_timing

    cli.main(["track", trip_file])
    assert engine.calls[0][2] is llm_timing


def test_trip_from_db_wins_over_yaml(storage, engine, trip_file):
    storage.upsert_trip("kapverden_2027", profile_to_dict(_profile()))
    assert cli.main(["run", "/does/not/exist.yaml", "--trip-from-db", "kapverden_2027"]) == 0
    assert len(engine.calls) == 1


def test_trip_from_db_missing_falls_back_to_yaml(storage, engine, trip_file):
    # Erster Lauf: Trip noch nicht in der DB -> YAML als Startwert
    assert cli.main(["run", str(trip_file), "--trip-from-db", "kapverden_2027"]) == 0
    assert len(engine.calls) == 1


def test_trip_from_db_unknown_name_is_profile_error(storage, engine, capsys):
    assert cli.main(["run", "--trip-from-db", "gibt_es_nicht"]) == 4
    assert engine.calls == []


def test_output_is_json_on_stdout_and_summary_on_stderr(storage, engine, trip_file, capsys):
    assert cli.main(["run", trip_file, "--no-llm"]) == 0
    out, err = capsys.readouterr()
    assert '"trip_name": "kapverden_2027"' in out
    assert "Bester Flug" in err and "412.50 EUR" in err and "Empfehlung: WATCH" in err


# --- Exit-Codes ---------------------------------------------------------------

@pytest.mark.parametrize(
    "error, code",
    [
        (MissingApiKeyError("kein Key"), 2),
        (NoFlightsError("leer"), 3),
        (ValueError("kaputt"), 4),
    ],
)
def test_exit_codes_for_engine_errors(storage, trip_file, monkeypatch, error, code):
    fake = FakeEngine(error=error)
    mod = types.ModuleType("services.engine")
    mod.run_trip = fake.run_trip
    mod.track_trip = fake.track_trip
    monkeypatch.setitem(sys.modules, "services.engine", mod)
    assert cli.main(["run", trip_file]) == code


def test_invalid_yaml_profile_gives_exit_4(storage, engine, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\norigins: [FRA]\n", encoding="utf-8")  # Pflichtfelder fehlen
    assert cli.main(["run", str(bad)]) == 4
    assert engine.calls == []
