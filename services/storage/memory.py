"""Rein in-memory Storage (Tests, lokal ohne DB)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from services.models import PricePoint, TripOption
from services.storage.base import CACHE_TTL_SECONDS


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _day_key(dt: datetime):
    return dt.date()


class MemoryStorage:
    def __init__(self, now: Callable[[], datetime] = _utcnow):
        self._now = now
        self._trips: dict[str, dict] = {}
        self._observations: list[dict] = []
        self._recommendations: list[dict] = []
        self._api_calls: list[dict] = []
        self._cache: dict[str, tuple[datetime, dict]] = {}

    # --- Trips -----------------------------------------------------------
    def get_trip(self, name: str) -> Optional[dict]:
        trip = self._trips.get(name)
        return dict(trip) if trip else None

    def upsert_trip(self, name: str, profile: dict) -> str:
        now = self._now()
        trip = self._trips.get(name)
        if trip is None:
            trip = {"id": str(uuid.uuid4()), "name": name, "created_at": now,
                    "tracking_enabled": True}
            self._trips[name] = trip
        trip["profile"] = profile
        trip["updated_at"] = now
        return trip["id"]

    def list_trips(self, tracking_only: bool = False) -> list[dict]:
        rows = [
            {
                "id": t["id"],
                "name": t["name"],
                "profile": t["profile"],
                "tracking_enabled": t.get("tracking_enabled", True),
            }
            for t in self._trips.values()
        ]
        if tracking_only:
            rows = [r for r in rows if r["tracking_enabled"]]
        return sorted(rows, key=lambda r: r["name"])

    # --- Beobachtungen ---------------------------------------------------
    def add_observations(
        self, trip_id: str, options: list[TripOption], observed_at: datetime
    ) -> None:
        for o in options:
            self._observations.append(
                {
                    "trip_id": trip_id,
                    "option_key": o.key,
                    "observed_at": observed_at,
                    "out_date": o.outbound.departure.date(),
                    "ret_date": o.inbound.departure.date(),
                    "out_origin": o.outbound.origin,
                    "out_destination": o.outbound.destination,
                    "ret_origin": o.inbound.origin,
                    "ret_destination": o.inbound.destination,
                    "price": o.price,
                    "airlines": sorted(set(o.outbound.airlines) | set(o.inbound.airlines)),
                    "out_stops": o.outbound.stops,
                    "ret_stops": o.inbound.stops,
                    "out_departure": o.outbound.departure,
                    "ret_departure": o.inbound.departure,
                    "source": o.source,
                }
            )

    def get_price_series(self, trip_id: str, option_key: str) -> list[PricePoint]:
        rows = [
            r
            for r in self._observations
            if r["trip_id"] == trip_id and r["option_key"] == option_key
        ]
        rows.sort(key=lambda r: r["observed_at"])
        return [PricePoint(r["observed_at"], r["price"]) for r in rows]

    def get_best_price_series(self, trip_id: str) -> list[PricePoint]:
        best: dict = {}  # Tag -> (observed_at, price) des Minimums
        for r in self._observations:
            if r["trip_id"] != trip_id:
                continue
            day = _day_key(r["observed_at"])
            cur = best.get(day)
            if cur is None or r["price"] < cur[1]:
                best[day] = (r["observed_at"], r["price"])
        return [PricePoint(at, p) for _, (at, p) in sorted(best.items())]

    # --- Empfehlungen ----------------------------------------------------
    def save_recommendation(self, trip_id: str, rec: dict) -> None:
        self._recommendations.append(
            {"trip_id": trip_id, "created_at": self._now(), "payload": rec}
        )

    def latest_recommendation(self, trip_id: str) -> Optional[dict]:
        rows = [r for r in self._recommendations if r["trip_id"] == trip_id]
        # max liefert bei Gleichstand den ersten -> reversed, damit der zuletzt gespeicherte gewinnt
        return max(reversed(rows), key=lambda r: r["created_at"])["payload"] if rows else None

    # --- API-Zaehler -----------------------------------------------------
    def record_api_call(self, provider: str, kind: str) -> None:
        self._api_calls.append(
            {"provider": provider, "kind": kind, "called_at": self._now()}
        )

    def api_calls_this_month(self, provider: str) -> int:
        now = self._now().astimezone(timezone.utc)
        return sum(
            1
            for c in self._api_calls
            if c["provider"] == provider
            and (lambda t: (t.year, t.month) == (now.year, now.month))(
                c["called_at"].astimezone(timezone.utc)
            )
        )

    # --- Cache -----------------------------------------------------------
    def cache_get(self, key: str) -> Optional[dict]:
        entry = self._cache.get(key)
        if entry is None:
            return None
        fetched_at, value = entry
        if self._now() - fetched_at > timedelta(seconds=CACHE_TTL_SECONDS):
            del self._cache[key]
            return None
        return value

    def cache_set(self, key: str, value: dict) -> None:
        self._cache[key] = (self._now(), value)
