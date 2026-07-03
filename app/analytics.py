"""Per-hit analytics: source attribution, UA parsing, and event recording.

`collect()` runs in the request path and only reads cheap header values.
`record_event()` runs in a BackgroundTask after the redirect is sent — it does
the UA parse + GeoIP lookup + INSERT, and swallows every error so analytics can
never break a redirect.
"""
from __future__ import annotations

import ipaddress
import logging

from fastapi import Request
from user_agents import parse as parse_ua_string

from . import db, geo

log = logging.getLogger("shorturl.analytics")


def _valid_ip(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        return None


def client_ip(request: Request) -> str | None:
    xri = _valid_ip(request.headers.get("x-real-ip"))
    if xri:
        return xri
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = _valid_ip(xff.split(",")[0])
        if first:
            return first
    return _valid_ip(request.client.host) if request.client else None


def classify_source(request: Request) -> str:
    if request.query_params.get("s") == "q":
        return "qr"
    if request.headers.get("referer"):
        return "link"
    return "direct"


def collect(request: Request) -> dict:
    """Cheap, request-time extraction. Safe to hand to a BackgroundTask."""
    return {
        "source": classify_source(request),
        "ip": client_ip(request),
        "user_agent": request.headers.get("user-agent"),
        "referrer": request.headers.get("referer"),
    }


def _parse_ua(ua_string: str | None) -> dict:
    if not ua_string:
        return {"device": None, "browser": None, "os": None, "is_bot": False}
    ua = parse_ua_string(ua_string)
    if ua.is_bot:
        device = "bot"
    elif ua.is_mobile:
        device = "mobile"
    elif ua.is_tablet:
        device = "tablet"
    elif ua.is_pc:
        device = "pc"
    else:
        device = "other"
    return {
        "device": device,
        "browser": (f"{ua.browser.family} {ua.browser.version_string}".strip() or None),
        "os": (f"{ua.os.family} {ua.os.version_string}".strip() or None),
        "is_bot": bool(ua.is_bot),
    }


def record_event(link_id: int, event: dict) -> None:
    """Insert one click_events row. Runs in a background threadpool task."""
    try:
        ua = _parse_ua(event.get("user_agent"))
        country, country_name, city = geo.lookup(event.get("ip"))
        with db.conn() as c, c.cursor() as cur:
            cur.execute(
                """INSERT INTO click_events
                       (link_id, source, ip, user_agent, device, browser, os,
                        is_bot, referrer, country, country_name, city)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    link_id, event.get("source"), event.get("ip"),
                    event.get("user_agent"), ua["device"], ua["browser"], ua["os"],
                    ua["is_bot"], event.get("referrer"), country, country_name, city,
                ),
            )
            c.commit()
    except Exception:  # noqa: BLE001 — analytics must never break a redirect
        log.exception("failed to record click_event for link_id=%s", link_id)
