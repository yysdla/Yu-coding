"""Shared indexing helpers."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)

