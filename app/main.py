import io
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import qrcode
from fastapi import Cookie, Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db
from .auth import SESSION_COOKIE, make_session, read_session, verify_credentials
from .config import settings
from .slug import is_valid_slug, random_slug

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.open_pool()
    yield
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
            SELECT id, slug, target_url, title, is_active, expires_at,
                   click_count, last_clicked_at, created_at
            FROM links
            ORDER BY created_at DESC
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
        {"user": user, "link": None, "error": None, "base_url": settings.base_url},
    )


@app.post("/admin/new")
def new_post(
    request: Request,
    user: str = Depends(require_user),
    target_url: str = Form(...),
    slug: str = Form(""),
    title: str = Form(""),
    expires_at: str = Form(""),
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

    if err:
        return templates.TemplateResponse(
            request,
            "link_form.html",
            {
                "user": user, "link": None, "error": err,
                "base_url": settings.base_url,
                "form": {"target_url": target_url, "slug": slug, "title": title, "expires_at": expires_at},
            },
            status_code=400,
        )

    with db.conn() as c, c.cursor() as cur:
        cur.execute("SELECT 1 FROM links WHERE slug = %s", (slug,))
        if cur.fetchone() is not None:
            return templates.TemplateResponse(
                request,
                "link_form.html",
                {
                    "user": user, "link": None,
                    "error": f"Slug '{slug}' is already taken.",
                    "base_url": settings.base_url,
                    "form": {"target_url": target_url, "slug": slug, "title": title, "expires_at": expires_at},
                },
                status_code=409,
            )
        cur.execute(
            """INSERT INTO links (slug, target_url, title, expires_at)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            (slug, norm_url, (title or None), exp),
        )
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
        {"user": user, "link": link, "error": None, "base_url": settings.base_url},
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

    if err:
        return templates.TemplateResponse(
            request,
            "link_form.html",
            {"user": user, "link": existing, "error": err, "base_url": settings.base_url},
            status_code=400,
        )

    active = is_active == "on"
    with db.conn() as c, c.cursor() as cur:
        if slug != existing["slug"]:
            cur.execute("SELECT 1 FROM links WHERE slug = %s AND id <> %s", (slug, link_id))
            if cur.fetchone() is not None:
                return templates.TemplateResponse(
                    request,
                    "link_form.html",
                    {"user": user, "link": existing,
                     "error": f"Slug '{slug}' is already taken.", "base_url": settings.base_url},
                    status_code=409,
                )
        cur.execute(
            """UPDATE links SET slug=%s, target_url=%s, title=%s, expires_at=%s, is_active=%s
               WHERE id=%s""",
            (slug, norm_url, (title or None), exp, active, link_id),
        )
        c.commit()
    return RedirectResponse("/admin/", status_code=303)


@app.post("/admin/{link_id}/delete")
def delete_link(link_id: int, user: str = Depends(require_user)) -> Response:
    with db.conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM links WHERE id = %s", (link_id,))
        c.commit()
    return RedirectResponse("/admin/", status_code=303)


@app.post("/admin/{link_id}/toggle")
def toggle_link(link_id: int, user: str = Depends(require_user)) -> Response:
    with db.conn() as c, c.cursor() as cur:
        cur.execute("UPDATE links SET is_active = NOT is_active WHERE id = %s", (link_id,))
        c.commit()
    return RedirectResponse("/admin/", status_code=303)


@app.get("/admin/{link_id}/qr.png")
def link_qr(link_id: int, user: str = Depends(require_user)) -> Response:
    with db.conn() as c, c.cursor() as cur:
        cur.execute("SELECT slug FROM links WHERE id = %s", (link_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Link not found")
    short_url = f"{settings.base_url.rstrip('/')}/{row['slug']}"
    img = qrcode.make(short_url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="{row["slug"]}.png"'},
    )


# ---------- public redirect (must be last; matches /{anything}) ----------

@app.get("/{slug}")
def redirect_slug(slug: str) -> Response:
    if not is_valid_slug(slug):
        raise HTTPException(status_code=404, detail="Not found")
    now = datetime.now(timezone.utc)
    with db.conn() as c, c.cursor() as cur:
        cur.execute(
            """UPDATE links
                  SET click_count = click_count + 1,
                      last_clicked_at = NOW()
                WHERE slug = %s
                  AND is_active
                  AND (expires_at IS NULL OR expires_at > NOW())
              RETURNING target_url""",
            (slug,),
        )
        row = cur.fetchone()
        c.commit()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return RedirectResponse(row["target_url"], status_code=307)


@app.get("/")
def root() -> Response:
    return RedirectResponse("/admin/", status_code=303)
