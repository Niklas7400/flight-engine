"""LLM-Zweitmeinung zum Buchungs-Timing (mit Websuche), streng validiert.

Vertrag: `llm_timing` gibt ein TimingAdvice(source="llm") oder None zurueck und wirft nie.
Die Antwort des Modells wird nur akzeptiert, wenn sie das Schema einhaelt und jeder
genannte Euro-Betrag einem gemessenen Preis entspricht (keine erfundenen Preise).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any, Optional

from services.llm import complete_json, last_search_used, last_sources
from services.models import (
    BookingAction, PriceInsights, PricePoint, TimingAdvice, TripOption,
)

logger = logging.getLogger(__name__)

MAX_SERIES = 30
MAX_SOURCES = 3
MAX_REASONS = 5
TOLERANCE_EUR = 2.0
TARGET_FACTOR = 3.0
CONFIDENCES = ("low", "medium", "high")

SYSTEM_PROMPT = (
    "Du bist ein vorsichtiger Berater fuer den Zeitpunkt von Flugbuchungen. "
    "Regeln:\n"
    "1. Erfinde keine Preise. Nenne in deiner Antwort nur Preise, die in den "
    "uebergebenen Fakten stehen (gemessene Preise) oder deinen eigenen Zielpreis.\n"
    "2. Recherchiere per Websuche nur Kontext, der in den Zahlen fehlt (Ferien, "
    "Flugplanaenderungen, saisonale Preisentwicklung), und nenne dafuer keine "
    "Preise aus dem Web.\n"
    "3. Wenn du unsicher bist, sage das ausdruecklich und setze confidence auf low.\n"
    "4. Antworte NUR als JSON-Objekt ohne weiteren Text im Format: "
    '{"action": "BUY_NOW|WAIT|WATCH", "decide_by": "YYYY-MM-DD oder null", '
    '"target_price": Zahl oder null, "confidence": "low|medium|high", '
    '"reasons": ["maximal 5 kurze deutsche Saetze"]}.'
)

_NUM = r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?"
_AMOUNT_RE = re.compile(
    rf"(?<![\d.,])({_NUM})\s*(?:€|EUR\b)|(?:€|\bEUR)\s*({_NUM})(?![\d])",
    re.IGNORECASE,
)


def _parse_amount(s: str) -> Optional[float]:
    s = s.strip()
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def _amounts(text: str) -> list[float]:
    out = []
    for m in _AMOUNT_RE.finditer(text):
        v = _parse_amount(m.group(1) or m.group(2))
        if v is not None:
            out.append(v)
    return out


def _known_prices(best: TripOption, series: list[PricePoint],
                  insights: Optional[PriceInsights], rules_advice: TimingAdvice,
                  own_target: Optional[float]) -> list[float]:
    known = [best.price] + [p.price for p in series]
    if insights is not None:
        known += [v for v in (insights.lowest_price, insights.typical_low,
                              insights.typical_high) if v is not None]
        known += [p for _, p in insights.history]
    if rules_advice.target_price is not None:
        known.append(rules_advice.target_price)
    if own_target is not None:
        known.append(own_target)
    return known


def _build_prompt(best: TripOption, series: list[PricePoint],
                  insights: Optional[PriceInsights], rules_advice: TimingAdvice,
                  today: date) -> str:
    dep = best.outbound.departure.date()
    ret = best.inbound.departure.date()
    airlines = sorted(set(best.outbound.airlines) | set(best.inbound.airlines))
    facts: dict[str, Any] = {
        "heute": today.isoformat(),
        "strecke": f"{best.outbound.origin} -> {best.outbound.destination}",
        "airlines": airlines,
        "abflug": dep.isoformat(),
        "rueckflug": ret.isoformat(),
        "tage_bis_abflug": (dep - today).days,
        "aktueller_preis_eur": round(best.price, 2),
        "preisreihe": [
            {"datum": p.observed_at.date().isoformat(), "preis_eur": round(p.price, 2)}
            for p in series[-MAX_SERIES:]
        ],
        "google_insights": None if insights is None else {
            "niedrigster_preis": insights.lowest_price,
            "preisniveau": insights.price_level,
            "typisch_niedrig": insights.typical_low,
            "typisch_hoch": insights.typical_high,
            "verlauf": [{"preis_eur": p} for _, p in insights.history],
        },
        "einschaetzung_regeln": {
            "action": rules_advice.action.value,
            "decide_by": rules_advice.decide_by.isoformat() if rules_advice.decide_by else None,
            "target_price": rules_advice.target_price,
            "reasons": rules_advice.reasons,
        },
    }
    return (
        "Gemessene Fakten zu einer Flugreise (JSON):\n"
        + json.dumps(facts, ensure_ascii=False, indent=1)
        + "\n\nAuftrag: Recherchiere per Websuche Kontext, der in den Zahlen fehlt: "
        f"Schulferien und Feiertage im Reisezeitraum (Abflugland: {best.outbound.origin}), "
        f"Flugplanaenderungen auf der Strecke {best.outbound.origin}–{best.outbound.destination} "
        f"(Airlines: {', '.join(_airlines(best)) or 'unbekannt'}), "
        "saisonale Preisentwicklung auf dieser Strecke. "
        "Gib dann deine eigene Empfehlung ab (nur JSON laut System-Vorgabe)."
    )


def _airlines(option: TripOption) -> list[str]:
    return list(dict.fromkeys(option.outbound.airlines + option.inbound.airlines))


def _validate(data: Any, best: TripOption, series: list[PricePoint],
              insights: Optional[PriceInsights], rules_advice: TimingAdvice,
              today: date) -> Optional[TimingAdvice]:
    if not isinstance(data, dict):
        return None
    try:
        action = BookingAction(data.get("action"))
    except ValueError:
        return None

    dep = best.outbound.departure.date()
    decide_by: Optional[date] = None
    raw_by = data.get("decide_by")
    if raw_by is not None:
        if not isinstance(raw_by, str):
            return None
        try:
            decide_by = date.fromisoformat(raw_by.strip())
        except ValueError:
            return None
        if not (today <= decide_by <= dep):
            return None

    target = data.get("target_price")
    if target is not None:
        if isinstance(target, bool) or not isinstance(target, (int, float)):
            return None
        target = float(target)
        if not (0 < target <= TARGET_FACTOR * best.price):
            return None

    confidence = data.get("confidence")
    if confidence not in CONFIDENCES:
        return None

    reasons = data.get("reasons")
    if (not isinstance(reasons, list) or not 1 <= len(reasons) <= MAX_REASONS
            or not all(isinstance(r, str) and r.strip() for r in reasons)):
        return None

    known = _known_prices(best, series, insights, rules_advice, target)
    for r in reasons:
        for amount in _amounts(r):
            if not any(abs(amount - k) <= TOLERANCE_EUR for k in known):
                logger.warning("LLM-Empfehlung verworfen: unbekannter Betrag %s", amount)
                return None

    reasons = [r.strip() for r in reasons]
    sources = [s for s in last_sources() if isinstance(s, str) and s][:MAX_SOURCES]
    if sources:
        reasons.append("Quellen: " + ", ".join(sources))
    if not last_search_used():
        reasons.append("Ohne Websuche erstellt: Aussagen zu Ferien und Flugplänen beruhen auf "
                       "dem Allgemeinwissen des Modells und sind nicht geprüft.")
    if action is BookingAction.BUY_NOW:
        decide_by = None  # "jetzt" braucht keine Frist
    return TimingAdvice(action=action, decide_by=decide_by, target_price=target,
                        confidence=confidence, reasons=reasons, source="llm")


def llm_timing(best: TripOption, series: list[PricePoint],
               insights: Optional[PriceInsights], rules_advice: TimingAdvice,
               today: date) -> Optional[TimingAdvice]:
    """Zweitmeinung des LLM (mit Websuche) oder None bei Fehler/Validierungsverstoss."""
    try:
        prompt = _build_prompt(best, series, insights, rules_advice, today)
        data = complete_json(prompt, SYSTEM_PROMPT, use_search=True)
        if data is None:
            return None
        return _validate(data, best, series, insights, rules_advice, today)
    except Exception as exc:  # nie Exception nach aussen
        logger.warning("llm_timing fehlgeschlagen: %s", type(exc).__name__)
        return None
