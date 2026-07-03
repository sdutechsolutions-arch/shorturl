"""GeoIP lookups via a local MaxMind/DB-IP .mmdb file.

The reader is opened once at startup. Every failure path (missing DB, private or
invalid IP, address not found) returns (None, None, None) — geo must never raise
into the request/redirect path.
"""
from __future__ import annotations

import logging
from pathlib import Path

import geoip2.database
import geoip2.errors

from .config import settings

log = logging.getLogger("shorturl.geo")

# Project root = parent of the app/ package dir; config paths are relative to it.
_ROOT = Path(__file__).resolve().parent.parent

_reader: geoip2.database.Reader | None = None


def open_reader() -> None:
    global _reader
    db_path = Path(settings.geoip_db_path)
    if not db_path.is_absolute():
        db_path = _ROOT / db_path
    if not db_path.exists():
        log.warning("GeoIP DB not found at %s; geo columns will be NULL", db_path)
        _reader = None
        return
    try:
        _reader = geoip2.database.Reader(str(db_path))
        log.info("GeoIP DB loaded from %s", db_path)
    except Exception:  # noqa: BLE001 — never fatal
        log.exception("failed to open GeoIP DB at %s", db_path)
        _reader = None


def close_reader() -> None:
    global _reader
    if _reader is not None:
        try:
            _reader.close()
        finally:
            _reader = None


def lookup(ip: str | None) -> tuple[str | None, str | None, str | None]:
    """Return (country_iso, country_name, city) or (None, None, None)."""
    if not ip or _reader is None:
        return (None, None, None)
    try:
        r = _reader.city(ip)
        return (r.country.iso_code, r.country.name, r.city.name)
    except (geoip2.errors.AddressNotFoundError, ValueError):
        return (None, None, None)
    except Exception:  # noqa: BLE001 — never fatal
        log.exception("geoip lookup failed for %s", ip)
        return (None, None, None)
