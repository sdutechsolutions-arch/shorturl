import re
import secrets
import string

ALPHABET = string.ascii_letters + string.digits

RESERVED = {
    "admin", "api", "static", "healthz", "favicon.ico", "robots.txt",
    "sitemap.xml", "login", "logout", "new", "edit", "_", "-",
}

SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def random_slug(length: int) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def is_valid_slug(s: str) -> bool:
    if not s or not SLUG_RE.match(s):
        return False
    if s.lower() in RESERVED:
        return False
    return True
