"""Deterministic ID hashing shared by the news and market-activity domains.

Mirrors the `hash_id` helper duplicated in engine/src/news.rs and
engine/src/market_activity.rs: 16-byte (32 hex char) SHA-256 over
pipe-separated parts, each part followed by its own separator (including
the last), so preserve that exact concatenation order if ID stability
across the migration ever matters.
"""

from __future__ import annotations

import hashlib


def hash_id(parts: list[str]) -> str:
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(part.encode("utf-8"))
        hasher.update(b"|")
    return hasher.hexdigest()[:32]
