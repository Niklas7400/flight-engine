"""Storage-Vertrag (siehe docs/ARCHITECTURE.md, Abschnitt services/storage/)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol

from services.models import PricePoint, TripOption

CACHE_TTL_SECONDS = 12 * 3600


class Storage(Protocol):
    def get_trip(self, name: str) -> Optional[dict]: ...

    def upsert_trip(self, name: str, profile: dict) -> str: ...

    def list_trips(self, tracking_only: bool = False) -> list[dict]:
        """Alle Trips (id, name, profile, tracking_enabled), nach name sortiert."""
        ...

    def add_observations(
        self, trip_id: str, options: list[TripOption], observed_at: datetime
    ) -> None: ...

    def get_price_series(self, trip_id: str, option_key: str) -> list[PricePoint]: ...

    def get_best_price_series(self, trip_id: str) -> list[PricePoint]:
        """Tagesminimum (Kalendertag von observed_at) ueber alle Optionen."""
        ...

    def save_recommendation(self, trip_id: str, rec: dict) -> None: ...

    def latest_recommendation(self, trip_id: str) -> Optional[dict]: ...

    def record_api_call(self, provider: str, kind: str) -> None: ...

    def api_calls_this_month(self, provider: str) -> int: ...

    def cache_get(self, key: str) -> Optional[dict]: ...

    def cache_set(self, key: str, value: dict) -> None: ...
