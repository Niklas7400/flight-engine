import pytest

from services.airports import resolve_airports
from services.decision import get_low_price_baseline, make_decision
from services.scoring import get_best_flight, score_flight


def flight(price, duration=2, stops=0):
    return {"price": price, "duration": duration, "stops": stops}


def test_resolve_airports_cities_and_codes():
    assert resolve_airports("London") == "LHR,LGW,STN,LTN"
    assert resolve_airports("fra, lhr") == "FRA,LHR"


def test_direct_flight_bonus_beats_small_price_difference():
    direct = flight(200, 2, 0)
    stop = flight(180, 4, 1)
    assert score_flight(direct) > score_flight(stop)
    assert get_best_flight([stop, direct])[0] is direct


def test_cheaper_flight_scores_higher_all_else_equal():
    assert score_flight(flight(100)) > score_flight(flight(200))


def test_baseline_uses_cheapest_30_percent():
    flights = [flight(p) for p in (100, 110, 120, 200, 300, 400, 500, 600, 700, 800)]
    assert get_low_price_baseline(flights) == pytest.approx(110)  # Mittel von 100, 110, 120


def test_baseline_with_fewer_than_four_flights_is_plain_average():
    # int(len * 0.3) == 0 -> es werden alle Preise gemittelt
    assert get_low_price_baseline([flight(100), flight(200)]) == 150


def test_decision_buy_when_far_below_market_and_history():
    flights = [flight(p) for p in (100, 300, 300, 300, 300, 300, 300, 300, 300, 300)]
    history = [[1, 300], [2, 300]]
    d = make_decision(100, flights, history)
    assert d["decision"] == "BUY"
    assert d["label"].endswith("Top Deal")
    assert d["reason"] == "cheaper than most current flights and below historical average"


def test_decision_wait_when_expensive():
    flights = [flight(p) for p in (100, 100, 100, 100, 100, 100, 100, 100, 100, 300)]
    d = make_decision(300, flights, [[1, 100]])
    assert d["decision"] == "WAIT"
    assert d["final_score"] < -5


def test_decision_without_history_uses_market_only():
    flights = [flight(p) for p in (100, 100, 100, 100)]
    d = make_decision(100, flights, [])
    assert d["timing_score"] == 0
    assert d["historical_avg"] == 0
    assert d["decision"] == "OK"
