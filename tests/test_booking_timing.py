from datetime import date, datetime, timedelta

import pytest

from services.booking_timing import (
    advise, easter_sunday, is_holiday_period, percentile, trend_14d,
)
from services.models import (
    BookingAction, Leg, PriceInsights, PricePoint, TripOption,
)

TODAY = date(2026, 11, 8)


def make_best(dep: date, price: float = 500.0) -> TripOption:
    out = Leg("FRA", "SID", datetime.combine(dep, datetime.min.time()),
              datetime.combine(dep, datetime.min.time()), 400, 0, ["TAP"], ["FRA", "SID"])
    back_day = dep + timedelta(days=10)
    back = Leg("SID", "FRA", datetime.combine(back_day, datetime.min.time()),
               datetime.combine(back_day, datetime.min.time()), 400, 0, ["TAP"], ["SID", "FRA"])
    return TripOption(out, back, price, "roundtrip")


def make_series(prices: list[float], end: date = TODAY) -> list[PricePoint]:
    """Ein Punkt pro Tag, letzter Punkt = end."""
    n = len(prices)
    return [PricePoint(datetime.combine(end - timedelta(days=n - 1 - i), datetime.min.time()), p)
            for i, p in enumerate(prices)]


FAR = date(2027, 9, 15)  # weit weg, nicht in Ferien (September)


def test_easter_years():
    assert easter_sunday(2027) == date(2027, 3, 28)
    assert easter_sunday(2024) == date(2024, 3, 31)
    assert easter_sunday(2025) == date(2025, 4, 20)
    assert easter_sunday(2026) == date(2026, 4, 5)
    assert easter_sunday(2038) == date(2038, 4, 25)


def test_holiday_detection():
    assert is_holiday_period(date(2027, 3, 21))
    assert is_holiday_period(date(2027, 4, 7))
    assert not is_holiday_period(date(2027, 4, 9))
    assert not is_holiday_period(date(2027, 3, 17))
    assert is_holiday_period(date(2026, 12, 20))
    assert is_holiday_period(date(2027, 1, 6))
    assert not is_holiday_period(date(2026, 12, 19))
    assert not is_holiday_period(date(2027, 1, 7))
    assert is_holiday_period(date(2027, 7, 1))
    assert is_holiday_period(date(2027, 8, 31))
    assert not is_holiday_period(date(2027, 9, 1))


def test_percentile():
    assert percentile([10, 20, 30, 40, 50], 20) == 18
    assert percentile([5], 20) == 5
    assert percentile([1, 2], 50) == 1.5


def test_rule1_departure_near():
    adv = advise(make_best(TODAY + timedelta(days=30)), make_series([500] * 3), None, TODAY, False)
    assert adv.action == BookingAction.BUY_NOW
    assert adv.confidence == "high"
    assert any("30 Tagen" in r for r in adv.reasons)


def test_rule1_holiday_threshold():
    dep = TODAY + timedelta(days=60)
    s = make_series([500] * 3)
    assert advise(make_best(dep), s, None, TODAY, True).action == BookingAction.BUY_NOW
    assert advise(make_best(dep), s, None, TODAY, False).action == BookingAction.WATCH


def test_rule2_typical_low():
    ins = PriceInsights(typical_low=390, typical_high=520)
    adv = advise(make_best(FAR, 385), make_series([450, 460]), ins, TODAY, False)
    assert adv.action == BookingAction.BUY_NOW
    assert ("Aktueller Preis 385 € liegt im unteren Bereich der von Google als typisch "
            "angegebenen Spanne (390–520 €).") in adv.reasons


def test_rule2_typical_low_boundary():
    ins = PriceInsights(typical_low=412, typical_high=520)
    adv = advise(make_best(FAR, 412), [], ins, TODAY, False)
    assert adv.action == BookingAction.BUY_NOW


def test_rule2_percentile():
    prices = [600, 590, 580, 570, 560, 550, 540, 530, 520, 510]
    adv = advise(make_best(FAR, 505), make_series(prices), None, TODAY, False)
    assert adv.action == BookingAction.BUY_NOW
    assert any("günstigsten 20 %" in r for r in adv.reasons)


def test_rule3_watch_empty():
    adv = advise(make_best(FAR), [], None, TODAY, False)
    assert adv.action == BookingAction.WATCH
    assert adv.confidence == "low"
    assert adv.target_price is None
    adv = advise(make_best(FAR), [], PriceInsights(), TODAY, False)
    assert adv.action == BookingAction.WATCH


def test_rule3_few_points():
    adv = advise(make_best(FAR), make_series([500, 510, 520, 530]), None, TODAY, False)
    assert adv.action == BookingAction.WATCH


def test_rule4_trend_up():
    # 14 Tage, linear von 400 auf 520 -> ca. +30 %; aktueller Preis ueber p20
    prices = [400 + 120 * i / 13 for i in range(14)]
    adv = advise(make_best(FAR, 520), make_series(prices), None, TODAY, False)
    assert adv.action == BookingAction.BUY_NOW
    assert adv.confidence == "medium"
    assert any("in den letzten 14 Tagen um" in r and "gestiegen" in r for r in adv.reasons)


def test_rule4_trend_down():
    prices = [520 - 120 * i / 13 for i in range(14)]
    # aktueller Preis oberhalb der Historie-Grenze, damit Regel 2 nicht greift
    adv = advise(make_best(FAR, 520), make_series(prices), None, TODAY, False)
    assert adv.action == BookingAction.WAIT
    assert any("gefallen" in r for r in adv.reasons)
    assert adv.target_price is not None


def test_trend_value():
    t = trend_14d(make_series([100 + i for i in range(14)]), TODAY)
    assert t == pytest.approx(13 / 100)
    assert trend_14d(make_series([100, 100]), TODAY) is None


def test_rule5_wait_with_target():
    prices = [500, 505, 500, 495, 500, 502, 498, 500]
    ins = PriceInsights(typical_low=450, typical_high=550)
    adv = advise(make_best(FAR, 500), make_series(prices), ins, TODAY, False)
    assert adv.action == BookingAction.WAIT
    assert adv.target_price == 450
    assert adv.decide_by == FAR - timedelta(days=45)
    assert adv.confidence == "medium"


def test_rule5_target_from_percentile():
    prices = [500, 505, 500, 495, 500, 502, 498, 500]
    adv = advise(make_best(FAR, 500), make_series(prices), None, TODAY, False)
    assert adv.action == BookingAction.WAIT
    assert adv.target_price == pytest.approx(percentile(prices, 20))


def test_decide_by_reached_buys():
    dep = TODAY + timedelta(days=45)  # decide_by == heute, aber nicht < 45
    prices = [500, 505, 500, 495, 500, 502, 498, 500]
    adv = advise(make_best(dep, 500), make_series(prices), None, TODAY, False)
    assert adv.action == BookingAction.BUY_NOW
    assert adv.decide_by is None  # "jetzt buchen" zeigt keine Frist
    assert any("Entscheidungsfrist" in r for r in adv.reasons)


def test_buy_now_never_has_deadline_and_basis_explains_confidence():
    """Echter Fall: Preis weit unter Googles Spanne -> BUY_NOW ohne 'entscheiden bis'."""
    dep = date(2027, 4, 4)
    ins = PriceInsights(typical_low=700, typical_high=1300)
    series = make_series([439, 404, 404, 369, 369, 369])
    adv = advise(make_best(dep, 369), series, ins, date(2026, 11, 8), True)
    assert adv.action == BookingAction.BUY_NOW
    assert adv.decide_by is None
    assert adv.basis == "6 Preispunkte, Google-Preisspanne"


def test_basis_without_range():
    adv = advise(make_best(date(2027, 9, 15)), make_series([500]), None, TODAY, False)
    assert adv.basis == "1 Preispunkt, keine Google-Preisspanne"
    assert adv.decide_by is not None  # WATCH/WAIT behalten die Frist


def test_decide_by_holiday():
    dep = date(2027, 3, 21)
    assert is_holiday_period(dep)
    adv = advise(make_best(dep), [], None, date(2026, 11, 8), True)
    assert adv.decide_by == date(2027, 1, 5)
    assert any("Osterferien" in r and "Nachfrage" in r for r in adv.reasons)
    assert not any("Charter" in r for r in adv.reasons)  # gilt fuer jede Strecke


def test_decide_by_normal():
    adv = advise(make_best(date(2027, 9, 15)), [], None, TODAY, False)
    assert adv.decide_by == date(2027, 8, 1)


def test_ranking_near_beats_cheap():
    ins = PriceInsights(typical_low=500, typical_high=600)
    adv = advise(make_best(TODAY + timedelta(days=10), 400), [], ins, TODAY, False)
    assert adv.action == BookingAction.BUY_NOW
    assert adv.confidence == "high"


def test_ranking_cheap_beats_watch_and_trend():
    ins = PriceInsights(typical_low=500, typical_high=600)
    adv = advise(make_best(FAR, 480), [], ins, TODAY, False)  # 0 Punkte, aber Range
    assert adv.action == BookingAction.BUY_NOW


def test_ranking_watch_beats_trend():
    # 4 Punkte stark steigend, keine Range -> WATCH statt Trend-Regel
    adv = advise(make_best(FAR, 500), make_series([400, 430, 460, 500]), None, TODAY, False)
    assert adv.action == BookingAction.WATCH


def test_confidence_high():
    prices = [500 + (i % 3) for i in range(15)]
    ins = PriceInsights(typical_low=400, typical_high=600)
    adv = advise(make_best(FAR, 501), make_series(prices), ins, TODAY, False)
    assert adv.confidence == "high"
