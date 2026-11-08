"""Supabase-Implementierung (supabase-py, Service-Role-Key)."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from services.models import PricePoint, TripOption
from services.storage.base import CACHE_TTL_SECONDS

_PAGE = 1000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class SupabaseStorage:
    def __init__(
        self,
        client: Any = None,
        now: Callable[[], datetime] = _utcnow,
    ):
        if client is None:
            from supabase import create_client  # lazy: Tests laufen ohne Paket

            client = create_client(
                os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
            )
        self._db = client
        self._now = now

    def _fetch_all(self, build) -> list[dict]:
        """Paginiert eine Abfrage; build(start, end) liefert die Query."""
        rows: list[dict] = []
        start = 0
        while True:
            chunk = build(start, start + _PAGE - 1).execute().data or []
            rows.extend(chunk)
            if len(chunk) < _PAGE:
                return rows
            start += _PAGE

    # --- Trips -----------------------------------------------------------
    def get_trip(self, name: str) -> Optional[dict]:
        data = self._db.table("trips").select("*").eq("name", name).limit(1).execute().data
        return data[0] if data else None

    def upsert_trip(self, name: str, profile: dict) -> str:
        data = (
            self._db.table("trips")
            .upsert({"name": name, "profile": profile}, on_conflict="name")
            .execute()
            .data
        )
        return data[0]["id"]

    # --- Beobachtungen ---------------------------------------------------
    def add_observations(
        self, trip_id: str, options: list[TripOption], observed_at: datetime
    ) -> None:
        if not options:
            return
        rows = [
            {
                "trip_id": trip_id,
                "option_key": o.key,
                "observed_at": observed_at.isoformat(),
                "out_date": o.outbound.departure.date().isoformat(),
                "ret_date": o.inbound.departure.date().isoformat(),
                "out_origin": o.outbound.origin,
                "out_destination": o.outbound.destination,
                "ret_origin": o.inbound.origin,
                "ret_destination": o.inbound.destination,
                "price": o.price,
                "airlines": sorted(set(o.outbound.airlines) | set(o.inbound.airlines)),
                "out_stops": o.outbound.stops,
                "ret_stops": o.inbound.stops,
                "out_departure": o.outbound.departure.isoformat(),
                "ret_departure": o.inbound.departure.isoformat(),
                "source": o.source,
            }
            for o in options
        ]
        self._db.table("price_observations").insert(rows).execute()

    def get_price_series(self, trip_id: str, option_key: str) -> list[PricePoint]:
        rows = self._fetch_all(
            lambda a, b: self._db.table("price_observations")
            .select("observed_at,price")
            .eq("trip_id", trip_id)
            .eq("option_key", option_key)
            .order("observed_at")
            .range(a, b)
        )
        return [PricePoint(_parse_ts(r["observed_at"]), float(r["price"])) for r in rows]

    def get_best_price_series(self, trip_id: str) -> list[PricePoint]:
        rows = self._fetch_all(
            lambda a, b: self._db.table("price_observations")
            .select("observed_at,price")
            .eq("trip_id", trip_id)
            .order("observed_at")
            .range(a, b)
        )
        best: dict = {}
        for r in rows:
            at, price = _parse_ts(r["observed_at"]), float(r["price"])
            day = at.date()
            if day not in best or price < best[day][1]:
                best[day] = (at, price)
        return [PricePoint(at, p) for _, (at, p) in sorted(best.items())]

    # --- Empfehlungen ----------------------------------------------------
    def save_recommendation(self, trip_id: str, rec: dict) -> None:
        self._db.table("recommendations").insert(
            {"trip_id": trip_id, "payload": rec}
        ).execute()

    def latest_recommendation(self, trip_id: str) -> Optional[dict]:
        data = (
            self._db.table("recommendations")
            .select("payload")
            .eq("trip_id", trip_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
            .data
        )
        return data[0]["payload"] if data else None

    # --- API-Zaehler -----------------------------------------------------
    def record_api_call(self, provider: str, kind: str) -> None:
        self._db.table("api_usage").insert({"provider": provider, "kind": kind}).execute()

    def api_calls_this_month(self, provider: str) -> int:
        now = self._now().astimezone(timezone.utc)
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        nxt = (start + timedelta(days=32)).replace(day=1)
        resp = (
            self._db.table("api_usage")
            .select("id", count="exact")
            .eq("provider", provider)
            .gte("called_at", start.isoformat())
            .lt("called_at", nxt.isoformat())
            .execute()
        )
        return int(resp.count or 0)

    # --- Cache -----------------------------------------------------------
    def cache_get(self, key: str) -> Optional[dict]:
        data = (
            self._db.table("search_cache")
            .select("value,fetched_at")
            .eq("key", key)
            .limit(1)
            .execute()
            .data
        )
        if not data:
            return None
        age = self._now() - _parse_ts(data[0]["fetched_at"])
        if age > timedelta(seconds=CACHE_TTL_SECONDS):
            return None
        return data[0]["value"]

    def cache_set(self, key: str, value: dict) -> None:
        self._db.table("search_cache").upsert(
            {"key": key, "value": value, "fetched_at": self._now().isoformat()},
            on_conflict="key",
        ).execute()
