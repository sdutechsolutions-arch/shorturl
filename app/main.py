import io
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Cookie, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image
from starlette.background import BackgroundTask

from . import analytics, db, geo, qr
from .auth import SESSION_COOKIE, make_session, read_session, verify_credentials
from .config import settings
from .slug import is_valid_slug, random_slug

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
BRAND_LOGO = BASE_DIR / "static" / "brand-logo.png"
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

EC_LEVELS = ["L", "M", "Q", "H"]


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.open_pool()
    geo.open_reader()
    _logo_dir().mkdir(parents=True, exist_ok=True)
    yield
    geo.close_reader()
    db.close_pool()


app = FastAPI(title="shorturl", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# ---------- auth ----------

def require_user(session: str | None = Cookie(default=None, alias=SESSION_COOKIE)) -> str:
    user = read_session(session)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    return user


def maybe_user(session: str | None = Cookie(default=None, alias=SESSION_COOKIE)) -> str | None:
    return read_session(session)


# ---------- helpers ----------

def _normalize_url(raw: str) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    if not parsed.netloc:
        return None
    return raw


def _parse_expiry(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _unique_random_slug() -> str:
    for _ in range(8):
        slug = random_slug(settings.slug_length)
        with db.conn() as c, c.cursor() as cur:
            cur.execute("SELECT 1 FROM links WHERE slug = %s", (slug,))
            if cur.fetchone() is None:
                return slug
    raise RuntimeError("could not allocate unique slug")


def _logo_dir() -> Path:
    p = Path(settings.logo_dir)
    return p if p.is_absolute() else ROOT_DIR / p


def _logo_path(link_id: int) -> Path:
    return _logo_dir() / f"{link_id}.png"


def _short_url(slug: str, *, qr_source: bool = False) -> str:
    base = settings.base_url.rstrip("/")
    return f"{base}/{slug}?s=q" if qr_source else f"{base}/{slug}"


def _clean_hex(value: str, default: str) -> str:
    v = (value or "").strip()
    if not v.startswith("#"):
        v = "#" + v
    if len(v) == 7 and all(ch in "0123456789abcdefABCDEF" for ch in v[1:]):
        return v.upper()
    return default


def _normalize_design(qr_fg: str, qr_bg: str, qr_size: int, qr_ec: str, *, with_logo: bool) -> dict:
    """Clean QR design fields. When a logo is present we force EC 'H' so the
    centre image doesn't knock out enough modules to break scanning."""
    fg = _clean_hex(qr_fg, "#000000")
    bg = _clean_hex(qr_bg, "#FFFFFF")
    size = min(2048, max(128, qr_size))
    ec = qr_ec.upper() if qr_ec.upper() in EC_LEVELS else "M"
    if with_logo:
        ec = "H"
    return {"fg": fg, "bg": bg, "size": size, "ec": ec}


def _read_logo_upload(logo: UploadFile | None) -> tuple[bytes | None, str | None]:
    """Return (raw_bytes, error). (None, None) when no file was uploaded."""
    if logo is None or not logo.filename:
        return None, None
    raw = logo.file.read()
    try:
        Image.open(io.BytesIO(raw)).verify()
        return raw, None
    except Exception:  # noqa: BLE001
        return None, "Logo must be a valid image file (PNG/JPG)."


def _save_logo(link_id: int, raw: bytes) -> None:
    _logo_dir().mkdir(parents=True, exist_ok=True)
    Image.open(io.BytesIO(raw)).convert("RGBA").save(_logo_path(link_id), format="PNG")


def _save_brand_logo(link_id: int) -> None:
    _logo_dir().mkdir(parents=True, exist_ok=True)
    Image.open(BRAND_LOGO).convert("RGBA").save(_logo_path(link_id), format="PNG")


# ---------- public ----------

@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


# ---------- admin: auth ----------

@app.get("/admin/login", response_class=HTMLResponse)
def login_get(request: Request, user: str | None = Depends(maybe_user)) -> Response:
    if user:
        return RedirectResponse("/admin/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/admin/login")
def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
) -> Response:
    if not verify_credentials(username, password):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid username or password."},
            status_code=401,
        )
    token = make_session(username)
    resp = RedirectResponse("/admin/", status_code=303)
    resp.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return resp


@app.post("/admin/logout")
def logout() -> Response:
    resp = RedirectResponse("/admin/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


# ---------- admin: links ----------

@app.get("/admin/", response_class=HTMLResponse)
@app.get("/admin", response_class=HTMLResponse)
def dashboard(request: Request, user: str = Depends(require_user)) -> Response:
    with db.conn() as c, c.cursor() as cur:
        cur.execute("""
            SELECT l.id, l.slug, l.target_url, l.title, l.is_active, l.expires_at,
                   l.click_count, l.last_clicked_at, l.created_at,
                   (SELECT COUNT(*) FROM click_events e
                      WHERE e.link_id = l.id AND e.source = 'qr') AS qr_scans,
                   (SELECT COUNT(*) FROM click_events e
                      WHERE e.link_id = l.id) AS event_count
            FROM links l
            ORDER BY l.created_at DESC
            LIMIT 500
        """)
        links = cur.fetchall()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"user": user, "links": links, "base_url": settings.base_url, "now": datetime.now(timezone.utc)},
    )


@app.get("/admin/new", response_class=HTMLResponse)
def new_get(request: Request, user: str = Depends(require_user)) -> Response:
    return templates.TemplateResponse(
        request,
        "link_form.html",
        {"user": user, "link": None, "error": None, "base_url": settings.base_url,
         "ec_levels": EC_LEVELS, "design": {"fg": "#000000", "bg": "#FFFFFF", "size": 512, "ec": "M"},
         "has_logo": False},
    )


@app.post("/admin/new")
def new_post(
    request: Request,
    user: str = Depends(require_user),
    target_url: str = Form(...),
    slug: str = Form(""),
    title: str = Form(""),
    expires_at: str = Form(""),
    qr_fg: str = Form("#000000"),
    qr_bg: str = Form("#FFFFFF"),
    qr_size: int = Form(512),
    qr_ec: str = Form("M"),
    use_brand_logo: str = Form("off"),
    logo: UploadFile | None = File(None),
) -> Response:
    err = None
    norm_url = _normalize_url(target_url)
    if not norm_url:
        err = "Target URL is required and must be a valid URL."

    slug = (slug or "").strip()
    if slug:
        if not is_valid_slug(slug):
            err = err or "Slug must be 1-64 chars (A-Z, a-z, 0-9, _, -) and not reserved."
    else:
        slug = _unique_random_slug()

    try:
        exp = _parse_expiry(expires_at)
    except ValueError:
        err = err or "Expires-at must be a valid ISO datetime."
        exp = None

    new_logo_bytes, logo_err = _read_logo_upload(logo)
    err = err or logo_err
    want_logo = new_logo_bytes is not None or use_brand_logo == "on"
    design = _normalize_design(qr_fg, qr_bg, qr_size, qr_ec, with_logo=want_logo)

    def _rerender(msg: str, status: int) -> Response:
        return templates.TemplateResponse(
            request,
            "link_form.html",
            {"user": user, "link": None, "error": msg, "base_url": settings.base_url,
             "ec_levels": EC_LEVELS, "design": design, "has_logo": False,
             "form": {"target_url": target_url, "slug": slug, "title": title, "expires_at": expires_at,
                      "use_brand_logo": use_brand_logo == "on"}},
            status_code=status,
        )

    if err:
        return _rerender(err, 400)

    with db.conn() as c, c.cursor() as cur:
        cur.execute("SELECT 1 FROM links WHERE slug = %s", (slug,))
        if cur.fetchone() is not None:
            return _rerender(f"Slug '{slug}' is already taken.", 409)
        cur.execute(
            """INSERT INTO links (slug, target_url, title, expires_at, qr_fg, qr_bg, qr_size, qr_ec)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (slug, norm_url, (title or None), exp, design["fg"], design["bg"], design["size"], design["ec"]),
        )
        new_id = cur.fetchone()["id"]
        if want_logo:
            if new_logo_bytes is not None:
                _save_logo(new_id, new_logo_bytes)
            else:
                _save_brand_logo(new_id)
            cur.execute("UPDATE links SET qr_logo = TRUE WHERE id = %s", (new_id,))
        c.commit()

    return RedirectResponse("/admin/", status_code=303)


@app.get("/admin/{link_id}/edit", response_class=HTMLResponse)
def edit_get(link_id: int, request: Request, user: str = Depends(require_user)) -> Response:
    with db.conn() as c, c.cursor() as cur:
        cur.execute("SELECT * FROM links WHERE id = %s", (link_id,))
        link = cur.fetchone()
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")
    return templates.TemplateResponse(
        request,
        "link_form.html",
        {"user": user, "link": link, "error": None, "base_url": settings.base_url,
         "ec_levels": EC_LEVELS, "has_logo": _logo_path(link_id).exists(),
         "design": {"fg": link["qr_fg"], "bg": link["qr_bg"], "size": link["qr_size"], "ec": link["qr_ec"]}},
    )


@app.post("/admin/{link_id}/edit")
def edit_post(
    link_id: int,
    request: Request,
    user: str = Depends(require_user),
    target_url: str = Form(...),
    slug: str = Form(...),
    title: str = Form(""),
    expires_at: str = Form(""),
    is_active: str = Form("off"),
    qr_fg: str = Form("#000000"),
    qr_bg: str = Form("#FFFFFF"),
    qr_size: int = Form(512),
    qr_ec: str = Form("M"),
    use_brand_logo: str = Form("off"),
    remove_logo: str = Form("off"),
    logo: UploadFile | None = File(None),
) -> Response:
    with db.conn() as c, c.cursor() as cur:
        cur.execute("SELECT * FROM links WHERE id = %s", (link_id,))
        existing = cur.fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Link not found")

    err = None
    norm_url = _normalize_url(target_url)
    if not norm_url:
        err = "Target URL is required and must be a valid URL."
    slug = slug.strip()
    if not is_valid_slug(slug):
        err = err or "Slug must be 1-64 chars (A-Z, a-z, 0-9, _, -) and not reserved."
    try:
        exp = _parse_expiry(expires_at)
    except ValueError:
        err = err or "Expires-at must be a valid ISO datetime."
        exp = None

    new_logo_bytes, logo_err = _read_logo_upload(logo)
    err = err or logo_err

    have_logo = _logo_path(link_id).exists()
    # decide the resulting logo state
    if new_logo_bytes is not None or use_brand_logo == "on":
        want_logo = True
    elif remove_logo == "on":
        want_logo = False
    else:
        want_logo = have_logo
    design = _normalize_design(qr_fg, qr_bg, qr_size, qr_ec, with_logo=want_logo)

    def _rerender(msg: str, status: int) -> Response:
        return templates.TemplateResponse(
            request,
            "link_form.html",
            {"user": user, "link": existing, "error": msg, "base_url": settings.base_url,
             "ec_levels": EC_LEVELS, "has_logo": have_logo, "design": design},
            status_code=status,
        )

    if err:
        return _rerender(err, 400)

    active = is_active == "on"

    # Apply logo file changes
    if new_logo_bytes is not None:
        _save_logo(link_id, new_logo_bytes)
        have_logo = True
    elif use_brand_logo == "on":
        _save_brand_logo(link_id)
        have_logo = True
    elif remove_logo == "on" and have_logo:
        _logo_path(link_id).unlink(missing_ok=True)
        have_logo = False

    with db.conn() as c, c.cursor() as cur:
        if slug != existing["slug"]:
            cur.execute("SELECT 1 FROM links WHERE slug = %s AND id <> %s", (slug, link_id))
            if cur.fetchone() is not None:
                return _rerender(f"Slug '{slug}' is already taken.", 409)
        cur.execute(
            """UPDATE links SET slug=%s, target_url=%s, title=%s, expires_at=%s, is_active=%s,
                   qr_fg=%s, qr_bg=%s, qr_size=%s, qr_ec=%s, qr_logo=%s
               WHERE id=%s""",
            (slug, norm_url, (title or None), exp, active,
             design["fg"], design["bg"], design["size"], design["ec"], have_logo, link_id),
        )
        c.commit()
    return RedirectResponse("/admin/", status_code=303)


@app.post("/admin/{link_id}/delete")
def delete_link(link_id: int, user: str = Depends(require_user)) -> Response:
    with db.conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM links WHERE id = %s", (link_id,))
        c.commit()
    _logo_path(link_id).unlink(missing_ok=True)
    return RedirectResponse("/admin/", status_code=303)


@app.post("/admin/{link_id}/toggle")
def toggle_link(link_id: int, user: str = Depends(require_user)) -> Response:
    with db.conn() as c, c.cursor() as cur:
        cur.execute("UPDATE links SET is_active = NOT is_active WHERE id = %s", (link_id,))
        c.commit()
    return RedirectResponse("/admin/", status_code=303)


# ---------- admin: QR ----------

def _qr_params(link_id: int, request: Request) -> tuple[str, dict]:
    """(short_url, render kwargs) from stored design, with optional query-param
    overrides so the edit form can preview unsaved changes."""
    with db.conn() as c, c.cursor() as cur:
        cur.execute("SELECT slug, qr_fg, qr_bg, qr_size, qr_ec, qr_logo FROM links WHERE id = %s", (link_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Link not found")
    q = request.query_params
    fg = _clean_hex(q.get("fg", row["qr_fg"]), row["qr_fg"])
    bg = _clean_hex(q.get("bg", row["qr_bg"]), row["qr_bg"])
    try:
        size = min(2048, max(128, int(q.get("size", row["qr_size"]))))
    except (TypeError, ValueError):
        size = row["qr_size"]
    ec = q.get("ec", row["qr_ec"]).upper()
    if ec not in EC_LEVELS:
        ec = row["qr_ec"]
    use_logo = row["qr_logo"] and _logo_path(link_id).exists()
    logo_path = str(_logo_path(link_id)) if use_logo else None
    return _short_url(row["slug"], qr_source=True), {
        "fg": fg, "bg": bg, "size": size, "ec": ec, "logo_path": logo_path,
    }


@app.get("/admin/{link_id}/qr.png")
def link_qr_png(link_id: int, request: Request, user: str = Depends(require_user)) -> Response:
    short_url, kw = _qr_params(link_id, request)
    png = qr.render_png(short_url, **kw)
    return Response(png, media_type="image/png",
                    headers={"Content-Disposition": f'inline; filename="qr-{link_id}.png"'})


@app.get("/admin/{link_id}/qr.svg")
def link_qr_svg(link_id: int, request: Request, user: str = Depends(require_user)) -> Response:
    short_url, kw = _qr_params(link_id, request)
    svg = qr.render_svg(short_url, **kw)
    return Response(svg, media_type="image/svg+xml",
                    headers={"Content-Disposition": f'inline; filename="qr-{link_id}.svg"'})


# ---------- admin: stats ----------

def _bars_svg(series: list[tuple[date, int, int]], width: int = 720, height: int = 160) -> str:
    """Daily bars: total hits (brand) with the QR portion (accent) overlaid."""
    if not series:
        return '<p class="muted">No hits in this window yet.</p>'
    pad_b, pad_t = 22, 8
    plot_h = height - pad_b - pad_t
    n = len(series)
    gap = 2
    bw = max(1.0, (width - (n - 1) * gap) / n)
    peak = max((row[1] for row in series), default=0) or 1
    bars = []
    for i, (d, total, qrn) in enumerate(series):
        x = i * (bw + gap)
        h = plot_h * total / peak
        y = pad_t + (plot_h - h)
        bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h:.1f}" '
                    f'fill="var(--brand)" rx="1"><title>{d.isoformat()}: {total} hits ({qrn} QR)</title></rect>')
        if qrn:
            hq = plot_h * qrn / peak
            yq = pad_t + (plot_h - hq)
            bars.append(f'<rect x="{x:.1f}" y="{yq:.1f}" width="{bw:.1f}" height="{hq:.1f}" '
                        f'fill="var(--accent)" rx="1"/>')
    labels = (f'<text x="0" y="{height-6}" class="axis">{series[0][0].isoformat()}</text>'
              f'<text x="{width}" y="{height-6}" text-anchor="end" class="axis">{series[-1][0].isoformat()}</text>')
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" preserveAspectRatio="none" '
            f'class="barchart">{"".join(bars)}{labels}</svg>')


@app.get("/admin/{link_id}/stats", response_class=HTMLResponse)
def link_stats(link_id: int, request: Request, user: str = Depends(require_user),
               days: int = 30) -> Response:
    days = min(3650, max(1, days))
    with db.conn() as c, c.cursor() as cur:
        cur.execute("SELECT * FROM links WHERE id = %s", (link_id,))
        link = cur.fetchone()
        if not link:
            raise HTTPException(status_code=404, detail="Link not found")

        cur.execute(
            """SELECT COUNT(*) AS total,
                      COUNT(*) FILTER (WHERE source='qr')     AS qr,
                      COUNT(*) FILTER (WHERE source='link')   AS link,
                      COUNT(*) FILTER (WHERE source='direct') AS direct,
                      COUNT(*) FILTER (WHERE NOT is_bot)      AS humans,
                      COUNT(*) FILTER (WHERE is_bot)          AS bots,
                      COUNT(DISTINCT ip)                      AS unique_ips
               FROM click_events
               WHERE link_id=%s AND ts >= NOW() - make_interval(days => %s)""",
            (link_id, days),
        )
        head = cur.fetchone()

        cur.execute(
            """SELECT date_trunc('day', ts)::date AS d,
                      COUNT(*) AS n,
                      COUNT(*) FILTER (WHERE source='qr') AS qr
               FROM click_events
               WHERE link_id=%s AND ts >= NOW() - make_interval(days => %s)
               GROUP BY 1 ORDER BY 1""",
            (link_id, days),
        )
        by_day = {r["d"]: (r["n"], r["qr"]) for r in cur.fetchall()}

        def top(column: str, limit: int = 10) -> list[dict]:
            cur.execute(
                f"""SELECT COALESCE(NULLIF({column}::text, ''), '(unknown)') AS label, COUNT(*) AS n
                    FROM click_events
                    WHERE link_id=%s AND ts >= NOW() - make_interval(days => %s)
                    GROUP BY 1 ORDER BY n DESC, label LIMIT %s""",
                (link_id, days, limit),
            )
            return cur.fetchall()

        breakdowns = {
            "source": top("source", 5),
            "device": top("device", 6),
            "browser": top("browser", 8),
            "os": top("os", 8),
            "country": top("country_name", 10),
            "city": top("city", 10),
            "referrer": top("referrer", 10),
        }

    # gap-filled daily series ending today (UTC)
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days - 1)
    series: list[tuple[date, int, int]] = []
    d = start
    while d <= today:
        n, qrn = by_day.get(d, (0, 0))
        series.append((d, n, qrn))
        d += timedelta(days=1)
    chart = _bars_svg(series)

    return templates.TemplateResponse(
        request,
        "stats.html",
        {"user": user, "link": link, "base_url": settings.base_url, "days": days,
         "head": head, "breakdowns": breakdowns, "chart": chart},
    )


# ---------- public redirect (must be last; matches /{anything}) ----------

@app.get("/{slug}")
def redirect_slug(slug: str, request: Request) -> Response:
    if not is_valid_slug(slug):
        raise HTTPException(status_code=404, detail="Not found")
    with db.conn() as c, c.cursor() as cur:
        cur.execute(
            """UPDATE links
                  SET click_count = click_count + 1,
                      last_clicked_at = NOW()
                WHERE slug = %s
                  AND is_active
                  AND (expires_at IS NULL OR expires_at > NOW())
              RETURNING id, target_url""",
            (slug,),
        )
        row = cur.fetchone()
        c.commit()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    event = analytics.collect(request)
    return RedirectResponse(
        row["target_url"],
        status_code=307,
        background=BackgroundTask(analytics.record_event, row["id"], event),
    )


@app.get("/")
def root() -> Response:
    return RedirectResponse("/admin/", status_code=303)
