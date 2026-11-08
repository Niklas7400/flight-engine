"""Gemeinsame Datenmodelle (Vertrag zwischen allen Modulen).

Alle Module tauschen nur diese Typen aus. Aenderungen hier nur bewusst und
mit Anpassung von docs/ARCHITECTURE.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import date, datetime
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Trip-Profil (Eingabe des Nutzers)
# ---------------------------------------------------------------------------

@dataclass
class Preferences:
    """Ja/Nein-Fragen. True = "stoert mich", False = "egal"."""

    avoid_early_departure: bool = True   # Abflug vor 07:00
    avoid_late_arrival: bool = True      # Ankunft nach 23:00 oder vor 05:00
    avoid_stops: bool = True             # Umstieg
    avoid_long_duration: bool = True     # > 1.5x oder > +3h ggue. schnellster Verbindung
    avoid_length_deviation: bool = False  # Abweichung von der Wunschdauer


@dataclass
class TripProfile:
    name: str                        # z. B. "kapverden_2027"
    origins: list[str]               # 1-3 IATA-Codes (nach Alias-Aufloesung)
    destinations: list[str]          # 1-3 IATA-Codes
    earliest_departure: date         # Fenster-Beginn (Hinflug)
    latest_return: date              # Fenster-Ende (Rueckflug)
    nights: int = 10                 # Wunschdauer
    min_nights: int = 8
    max_nights: int = 12
    flex_days: int = 1               # weiche Fenstergrenze: so viele Tage ausserhalb erlaubt
    allow_open_jaw: bool = False     # Rueckflug von anderer Insel als Ankunft
    preferences: Preferences = field(default_factory=Preferences)
    currency: str = "EUR"


# ---------------------------------------------------------------------------
# Flugdaten
# ---------------------------------------------------------------------------

@dataclass
class Leg:
    """Eine Richtung (Hin- ODER Rueckflug), ggf. mit Umstiegen."""

    origin: str              # IATA erster Abflughafen
    destination: str         # IATA letzter Ankunftsflughafen
    departure: datetime      # lokale Zeit Abflug
    arrival: datetime        # lokale Zeit Ankunft
    duration_min: int        # Gesamtreisezeit in Minuten
    stops: int
    airlines: list[str]
    route: list[str]         # ["FRA", "LIS", "SID"]


@dataclass
class OneWayOffer:
    leg: Leg
    price: float
    booking_token: Optional[str] = None


@dataclass
class PriceInsights:
    """Aus SerpApi `price_insights`. Alle Felder optional."""

    lowest_price: Optional[float] = None
    price_level: Optional[str] = None                 # "low" | "typical" | "high"
    typical_low: Optional[float] = None
    typical_high: Optional[float] = None
    history: list[tuple[int, float]] = field(default_factory=list)  # (unix_ts, preis)


@dataclass
class SearchResult:
    offers: list[OneWayOffer]
    insights: PriceInsights
    from_cache: bool = False
    unpriced: list[Leg] = field(default_factory=list)  # Fluege, fuer die Google keinen Preis zeigt


@dataclass
class TripOption:
    """Kombination Hin + Rueck (aus zwei One-Ways oder einem echten Roundtrip)."""

    outbound: Leg
    inbound: Leg
    price: float                      # Gesamtpreis 1 Person
    source: str                       # "oneway_combo" | "roundtrip"
    booking_tokens: list[str] = field(default_factory=list)
    # vom Utility-Scoring befuellt:
    effective_cost: Optional[float] = None
    penalties: dict[str, float] = field(default_factory=dict)  # key -> EUR

    @property
    def nights(self) -> int:
        return (self.inbound.departure.date() - self.outbound.arrival.date()).days

    @property
    def key(self) -> str:
        """Stabiler Schluessel fuer Tracking: Datum + Flughaefen."""
        return (
            f"{self.outbound.origin}-{self.outbound.destination}-{self.outbound.departure.date()}"
            f"_{self.inbound.origin}-{self.inbound.destination}-{self.inbound.departure.date()}"
        )


# ---------------------------------------------------------------------------
# Empfehlung
# ---------------------------------------------------------------------------

class BookingAction(str, Enum):
    BUY_NOW = "BUY_NOW"
    WAIT = "WAIT"          # warten, aber mit Zielpreis und Deadline
    WATCH = "WATCH"        # zu wenig Daten, weiter beobachten


@dataclass
class PricePoint:
    observed_at: datetime
    price: float


@dataclass
class TimingAdvice:
    action: BookingAction
    decide_by: Optional[date]          # spaetestens bis dann buchen (nie bei BUY_NOW)
    target_price: Optional[float]      # bei WAIT: unter diesem Preis sofort buchen
    confidence: str                    # "low" | "medium" | "high"; Regeln: Datenmenge, LLM: Selbsteinschaetzung
    reasons: list[str]                 # deutsche, kurze Saetze
    source: str = "rules"              # "rules" | "llm"
    basis: Optional[str] = None        # Regeln: worauf confidence beruht, z. B. "8 Preispunkte, Google-Preisspanne"


@dataclass
class Recommendation:
    trip_name: str
    created_at: datetime
    best: TripOption
    cheapest: TripOption
    alternatives: list[TripOption]     # Top-N nach effective_cost (ohne best)
    explanation: str                   # warum best statt cheapest (deutsch)
    timing_rules: TimingAdvice
    timing_llm: Optional[TimingAdvice] = None
    api_calls_used: int = 0
    api_calls_remaining_month: Optional[int] = None
    hints: list[str] = field(default_factory=list)  # z. B. Direktfluege ohne Preis bei Google


def to_jsonable(obj):
    """Dataclasses/datetime/Enum -> JSON-kompatible Struktur (fuer DB und API)."""
    if hasattr(obj, "__dataclass_fields__"):
        data = {f.name: to_jsonable(getattr(obj, f.name)) for f in fields(obj)}
        if isinstance(obj, TripOption):
            data["nights"] = obj.nights
            data["key"] = obj.key
        return data
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    return obj
