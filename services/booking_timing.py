"""Buchungs-Timing: deterministische Regeln, wann gebucht werden sollte."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from services.models import (
    BookingAction,
    PriceInsights,
    PricePoint,
    TimingAdvice,
    TripOption,
)

MIN_POINTS = 5
HIGH_POINTS = 15
TREND_DAYS = 14
TREND_THRESHOLD = 0.05
NEAR_DAYS = 45
NEAR_DAYS_HOLIDAY = 75


def easter_sunday(year: int) -> date:
    """Ostersonntag (anonymer gregorianischer Algorithmus / Meeus-Jones-Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, dd = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, dd + 1)


def holiday_name(day: date) -> Optional[str]:
    """Name der Ferienzeit oder None. Ostern +-10 Tage, 20.12.-6.1., 1.7.-31.8."""
    easter = easter_sunday(day.year)
    if abs((day - easter).days) <= 10:
        return "Osterferien"
    if (day.month == 12 and day.day >= 20) or (day.month == 1 and day.day <= 6):
        return "Weihnachtsferien"
    if day.month in (7, 8):
        return "Sommerferien"
    return None


def is_holiday_period(day: date) -> bool:
    return holiday_name(day) is not None


def percentile(values: list[float], pct: float) -> float:
    """Perzentil (0-100) mit linearer Interpolation, ohne numpy."""
    if not values:
        raise ValueError("Leere Werteliste")
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * pct / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def trend_14d(series: list[PricePoint], today: date) -> Optional[float]:
    """Relative Preisaenderung ueber die letzten 14 Tage (0.07 = +7 %).

    Methode: lineare Regression (kleinste Quadrate) ueber alle Punkte der letzten
    14 Tage (Tage als x-Achse); Aenderung = (Fit am letzten Punkt - Fit am
    ersten Punkt) / Fit am ersten Punkt. Benoetigt >= 3 Punkte an >= 2 Tagen,
    sonst None.
    """
    start = today - timedelta(days=TREND_DAYS)
    pts = [((p.observed_at.date() - start).days, p.price)
           for p in series if start <= p.observed_at.date() <= today]
    if len(pts) < 3 or len({x for x, _ in pts}) < 2:
        return None
    n = len(pts)
    mx = sum(x for x, _ in pts) / n
    my = sum(y for _, y in pts) / n
    sxx = sum((x - mx) ** 2 for x, _ in pts)
    slope = sum((x - mx) * (y - my) for x, y in pts) / sxx
    x0 = min(x for x, _ in pts)
    x1 = max(x for x, _ in pts)
    fit_start = my + slope * (x0 - mx)
    fit_end = my + slope * (x1 - mx)
    if fit_start <= 0:
        return None
    return (fit_end - fit_start) / fit_start


def _eur(x: float) -> str:
    return f"{round(x)} €"


def advise(best: TripOption, series: list[PricePoint], insights: Optional[PriceInsights],
           today: date, holiday_period: bool) -> TimingAdvice:
    """Buchungsempfehlung. Rangfolge (erste zutreffende Regel bestimmt die Aktion):

    1. Abflug nah (< 45 Tage, bei holiday_period < 75) -> BUY_NOW, confidence high.
    2. Preis guenstig (<= typical_low oder <= 20. Perzentil der eigenen Historie
       bei >= 5 Punkten) -> BUY_NOW.
    3. Zu wenig Daten (< 5 Punkte und keine typical-Range) -> WATCH, confidence low.
    4. Trend 14 Tage (lineare Regression, siehe trend_14d) > +5 % -> BUY_NOW
       (medium); < -5 % -> WAIT.
    5. Sonst WAIT mit target_price (20. Perzentil bzw. typical_low) und decide_by.
    Ausnahme: ist decide_by <= heute, wird WAIT zu BUY_NOW.

    decide_by = Abflug - 75 Tage (holiday) bzw. - 45 Tage. confidence: high bei
    >= 15 Punkten und Insights, medium bei >= 5, sonst low (Regel 1: high,
    Regel 3: low). Zu jeder ausgeloesten Regel gibt es einen Satz in reasons.
    """
    dep = best.outbound.departure.date()
    days = (dep - today).days
    near = NEAR_DAYS_HOLIDAY if holiday_period else NEAR_DAYS
    decide_by = dep - timedelta(days=near)
    price = best.price
    n = len(series)
    hist = [p.price for p in series]
    has_range = insights is not None and (
        insights.typical_low is not None or insights.typical_high is not None)
    typical_low = insights.typical_low if insights else None
    typical_high = insights.typical_high if insights else None

    if n >= HIGH_POINTS and insights is not None:
        confidence = "high"
    elif n >= MIN_POINTS:
        confidence = "medium"
    else:
        confidence = "low"

    p20 = percentile(hist, 20) if n >= MIN_POINTS else None
    target = typical_low if typical_low is not None else p20

    reasons: list[str] = []
    if holiday_period:
        name = holiday_name(dep) or "Ferienzeit"
        reasons.append(f"Reise fällt in die {name}: Die Nachfrage ist hoch, "
                       "günstige Plätze sind oft früh vergeben.")

    # Regel 1
    if days < near:
        reasons.insert(0, f"Abflug in {max(days, 0)} Tagen: "
                          "Preise steigen erfahrungsgemäß kurz vor Abflug.")
    # Regel 2
    cheap = False
    if typical_low is not None and price <= typical_low:
        cheap = True
        if typical_high is not None:
            reasons.append(
                f"Aktueller Preis {_eur(price)} liegt im unteren Bereich der von Google als "
                f"typisch angegebenen Spanne ({round(typical_low)}–{_eur(typical_high)}).")
        else:
            reasons.append(f"Aktueller Preis {_eur(price)} liegt unter dem von Google als "
                           f"typisch angegebenen Mindestpreis ({_eur(typical_low)}).")
    if p20 is not None and price <= p20:
        cheap = True
        reasons.append(f"Aktueller Preis {_eur(price)} gehört zu den günstigsten 20 % "
                       f"der bisher beobachteten Preise (Grenze {_eur(p20)}).")

    if days < near:
        return TimingAdvice(BookingAction.BUY_NOW, decide_by, None, "high", reasons)
    if cheap:
        return TimingAdvice(BookingAction.BUY_NOW, decide_by, None, confidence, reasons)

    # Regel 3
    if n < MIN_POINTS and not has_range:
        reasons.append(f"Erst {n} Preispunkte und keine typische Preisspanne: "
                       "Preis weiter beobachten.")
        return TimingAdvice(BookingAction.WATCH, decide_by, None, "low", reasons)

    # Regel 4
    trend = trend_14d(series, today)
    if trend is not None and trend > TREND_THRESHOLD:
        reasons.append(f"Preis ist in den letzten 14 Tagen um {round(trend * 100)} % gestiegen.")
        return TimingAdvice(BookingAction.BUY_NOW, decide_by, None, "medium", reasons)
    if trend is not None and trend < -TREND_THRESHOLD:
        reasons.append(f"Preis ist in den letzten 14 Tagen um {round(-trend * 100)} % gefallen.")
        action = BookingAction.WAIT
    else:
        action = BookingAction.WAIT
        reasons.append("Kein deutliches Preissignal: weiter warten.")

    # Regel 5
    if decide_by <= today:
        reasons.append(f"Die Entscheidungsfrist ({decide_by.strftime('%d.%m.%Y')}) ist erreicht.")
        return TimingAdvice(BookingAction.BUY_NOW, decide_by, None, confidence, reasons)
    if target is not None:
        reasons.append(f"Unter {_eur(target)} sofort buchen, spätestens bis "
                       f"{decide_by.strftime('%d.%m.%Y')}.")
    return TimingAdvice(action, decide_by, target, confidence, reasons)
