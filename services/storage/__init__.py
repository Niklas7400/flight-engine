"""Storage-Schicht: get_storage() waehlt Supabase oder In-Memory."""
from __future__ import annotations

import os

from services.storage.base import Storage
from services.storage.memory import MemoryStorage

__all__ = ["get_storage", "Storage", "MemoryStorage"]


def get_storage() -> Storage:
    """Supabase wenn SUPABASE_URL gesetzt, sonst Memory."""
    if os.environ.get("SUPABASE_URL"):
        from services.storage.supabase_store import SupabaseStorage

        return SupabaseStorage()
    return MemoryStorage()
