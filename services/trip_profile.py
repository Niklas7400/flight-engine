"""Trip-Profil laden, validieren und serialisieren.

Vertrag: docs/ARCHITECTURE.md (Abschnitt services/trip_profile.py).
Datentypen: services/models.py.
"""
from __future__ import annotations

import re
from dataclasses import fields
from datetime import date, datetime
from typing import Any

import yaml

from services.airports import resolve_airports
from services.models import Preferences, TripProfile, to_jsonable

_IATA = re.compile(r"[A-Z]{3}")
_MAX_AIRPORTS = 3
_FLEX_DAYS_RANGE = (0, 3)

_PROFILE_KEYS = {
    "name",
    "origins",
    "destinations",
    "earliest_departure",
    "latest_return",
    "nights",
    "min_nights",
    "max_nights",
    "flex_days",
    "allow_open_jaw",
    "preferences",
    "currency",
}
_REQUIRED_KEYS = {"name", "origins", "destinations", "earliest_departure", "latest_return"}
_PREFERENCE_KEYS = {f.name for f in fields(Preferences)}


def load_profile(path: str) -> TripProfile:
    """Liest eine YAML-Datei und liefert das validierte Trip-Profil."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML-Fehler in {path}: {exc}") from exc
    return profile_from_dict(data)


def profile_from_dict(d: dict) -> TripProfile:
    """Baut ein validiertes TripProfile aus einem Dict (z. B. aus YAML)."""
    if not isinstance(d, dict):
        raise ValueError("Trip-Profil muss ein Mapping (Schlüssel: Wert) sein.")
    _reject_unknown(d, _PROFILE_KEYS, "Trip-Profil")
    missing = sorted(_REQUIRED_KEYS - d.keys())
    if missing:
        raise ValueError(f"Pflichtfeld(er) fehlen: {', '.join(missing)}")

    origins = _airports(d["origins"], "origins")
    destinations = _airports(d["destinations"], "destinations")
    earliest = _date(d["earliest_departure"], "earliest_departure")
    latest = _date(d["latest_return"], "latest_return")
    if not earliest < latest:
        raise ValueError(
            f"earliest_departure ({earliest.isoformat()}) muss vor latest_return "
            f"({latest.isoformat()}) liegen."
        )

    # Defaults wie in models.TripProfile
    nights = _int(d.get("nights", 10), "nights")
    min_nights = _int(d.get("min_nights", 8), "min_nights")
    max_nights = _int(d.get("max_nights", 12), "max_nights")
    if not min_nights <= nights <= max_nights:
        raise ValueError(
            f"nights ({nights}) muss zwischen min_nights ({min_nights}) und "
            f"max_nights ({max_nights}) liegen."
        )

    flex_days = _int(d.get("flex_days", 1), "flex_days")
    low, high = _FLEX_DAYS_RANGE
    if not low <= flex_days <= high:
        raise ValueError(f"flex_days muss zwischen {low} und {high} liegen, ist {flex_days}.")

    preferences = _preferences(d.get("preferences") or {})

    return TripProfile(
        name=_str(d["name"], "name"),
        origins=origins,
        destinations=destinations,
        earliest_departure=earliest,
        latest_return=latest,
        nights=nights,
        min_nights=min_nights,
        max_nights=max_nights,
        flex_days=flex_days,
        allow_open_jaw=_bool(d.get("allow_open_jaw", False), "allow_open_jaw"),
        preferences=preferences,
        currency=_str(d.get("currency", "EUR"), "currency"),
    )


def profile_to_dict(p: TripProfile) -> dict:
    """Gegenstück zu profile_from_dict: Daten als ISO-Strings, preferences als dict."""
    return to_jsonable(p)


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _reject_unknown(d: dict, allowed: set[str], where: str) -> None:
    unknown = sorted(str(k) for k in d.keys() if k not in allowed)
    if unknown:
        raise ValueError(f"Unbekannte Felder in {where}: {', '.join(unknown)}")


def _airports(value: Any, key: str) -> list[str]:
    """Liste oder kommagetrennter String (inkl. Städte-Alias) -> eindeutige IATA-Codes."""
    if isinstance(value, list):
        if not all(isinstance(v, str) for v in value):
            raise ValueError(f"{key} darf nur Texte (Flughafen-Codes oder Städte) enthalten.")
        raw = ",".join(value)
    elif isinstance(value, str):
        raw = value
    else:
        raise ValueError(f"{key} muss eine Liste oder ein kommagetrennter Text sein.")

    codes: list[str] = []
    for code in resolve_airports(raw).split(","):
        code = code.strip()
        if code and code not in codes:
            codes.append(code)

    if not 1 <= len(codes) <= _MAX_AIRPORTS:
        raise ValueError(
            f"{key}: 1 bis {_MAX_AIRPORTS} Flughäfen erwartet, gefunden: {len(codes)}."
        )
    for code in codes:
        if not _IATA.fullmatch(code):
            raise ValueError(
                f"{key}: ungültiger Flughafen-Code '{code}' (erwartet 3 Großbuchstaben)."
            )
    return codes


def _date(value: Any, key: str) -> date:
    if isinstance(value, datetime):
        raise ValueError(f"{key} muss ein Datum ohne Uhrzeit sein (YYYY-MM-DD).")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError(f"Ungültiges Datum für {key}: {value!r} (erwartet YYYY-MM-DD).")


def _int(value: Any, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} muss eine ganze Zahl sein, ist {value!r}.")
    return value


def _bool(value: Any, key: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{key} muss true oder false sein, ist {value!r}.")
    return value


def _str(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} muss ein nicht leerer Text sein.")
    return value


def _preferences(raw: Any) -> Preferences:
    if not isinstance(raw, dict):
        raise ValueError("preferences muss ein Mapping (Schlüssel: true/false) sein.")
    _reject_unknown(raw, _PREFERENCE_KEYS, "preferences")
    return Preferences(**{k: _bool(v, f"preferences.{k}") for k, v in raw.items()})
