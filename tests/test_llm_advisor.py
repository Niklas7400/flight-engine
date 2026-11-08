from datetime import date, datetime

import pytest

from services import llm_advisor
from services.models import (
    BookingAction, Leg, PriceInsights, PricePoint, TimingAdvice, TripOption,
)

TODAY = date(2026, 11, 8)
DEP = date(2027, 2, 10)


def make_best(price=500.0):
    out = Leg("FRA", "SID", datetime(2027, 2, 10, 8), datetime(2027, 2, 10, 14),
              400, 0, ["Condor"], ["FRA", "SID"])
    inn = Leg("SID", "FRA", datetime(2027, 2, 20, 8), datetime(2027, 2, 20, 14),
              400, 0, ["Condor"], ["SID", "FRA"])
    return TripOption(out, inn, price, "roundtrip")


SERIES = [PricePoint(datetime(2026, 10, 1 + i), 480.0 + i) for i in range(5)]
INSIGHTS = PriceInsights(lowest_price=450.0, price_level="typical",
                         typical_low=430.0, typical_high=620.0,
                         history=[(1, 455.0)])
RULES = TimingAdvice(BookingAction.WAIT, date(2026, 12, 1), 440.0, "medium",
                     ["Regel-Grund"])


def good(**over):
    d = {"action": "WAIT", "decide_by": "2026-12-01", "target_price": 450.0,
         "confidence": "medium", "reasons": ["Preis liegt bei 500 € im Mittelfeld."]}
    d.update(over)
    return d


@pytest.fixture
def run(monkeypatch):
    def _run(resp, sources=(), insights=INSIGHTS):
        monkeypatch.setattr(llm_advisor, "complete_json", lambda *a, **k: resp)
        monkeypatch.setattr(llm_advisor, "last_sources", lambda: list(sources))
        return llm_advisor.llm_timing(make_best(), SERIES, insights, RULES, TODAY)
    return _run


def test_valid(run):
    a = run(good())
    assert a.source == "llm" and a.action is BookingAction.WAIT
    assert a.decide_by == date(2026, 12, 1) and a.target_price == 450.0
    assert a.confidence == "medium"
    assert len(a.reasons) == 1


def test_sources_appended_max3(run):
    a = run(good(), sources=["u1", "u2", "u3", "u4"])
    assert a.reasons[-1] == "Quellen: u1, u2, u3"


def test_null_fields_ok(run):
    a = run(good(action="WATCH", decide_by=None, target_price=None))
    assert a.decide_by is None and a.target_price is None


@pytest.mark.parametrize("over", [
    {"action": "HOLD"},
    {"decide_by": "kaputt"},
    {"decide_by": "2026-11-07"},
    {"decide_by": "2027-02-11"},
    {"target_price": 0},
    {"target_price": -5},
    {"target_price": 1501},
    {"target_price": "viel"},
    {"confidence": "sehr hoch"},
    {"reasons": []},
    {"reasons": "text"},
    {"reasons": ["a"] * 6},
    {"reasons": [1]},
])
def test_invalid(run, over):
    assert run(good(**over)) is None


def test_boundaries_ok(run):
    assert run(good(decide_by="2026-11-08")) is not None
    assert run(good(decide_by="2027-02-10", target_price=1500)) is not None


@pytest.mark.parametrize("text", [
    "Nur noch 399 € moeglich.", "Bei €399 kaufen.", "Ab 399 EUR.", "Ab 399,50 €.",
])
def test_invented_amount(run, text):
    assert run(good(reasons=[text])) is None


@pytest.mark.parametrize("text", [
    "Typisch ab 430 € bis 620 €.", "Tief war 450,00 €.", "Ziel 451 EUR.",
    "Serie 482 €.", "Verlauf 455 €.", "Regeln sagen EUR 440.", "Eigenes Ziel €450.",
])
def test_known_amounts(run, text):
    assert run(good(reasons=[text])) is not None


def test_year_and_percent_not_amounts(run):
    a = run(good(reasons=["Winterferien 2027 beginnen frueh, Preise +12 % moeglich."]))
    assert a is not None


def test_no_insights(run):
    assert run(good(reasons=["Typisch 430 €."]), insights=None) is None


def test_none_from_llm(run):
    assert run(None) is None


def test_prompt_contains_facts(monkeypatch):
    seen = {}

    def fake(prompt, system, use_search=False):
        seen.update(prompt=prompt, system=system, use_search=use_search)
        return None

    monkeypatch.setattr(llm_advisor, "complete_json", fake)
    monkeypatch.setattr(llm_advisor, "last_sources", lambda: [])
    series = [PricePoint(datetime(2026, 9, 1 + i % 28), 400.0 + i) for i in range(40)]
    llm_advisor.llm_timing(make_best(), series, INSIGHTS, RULES, TODAY)
    p = seen["prompt"]
    assert seen["use_search"] is True
    for s in ("FRA", "SID", "Condor", "2027-02-10", "2027-02-20", "94", "500.0",
              "typisch_niedrig", "430", "WAIT", "2026-12-01", "440", "Regel-Grund",
              "Hessen", "Binter"):
        assert s in p
    assert p.count('"preis_eur"') == 30 + 1 + 0 or p.count('"datum"') == 30
    assert '"preis_eur": 439.0' in p and '"preis_eur": 409.0' not in p
    assert "JSON" in seen["system"]
