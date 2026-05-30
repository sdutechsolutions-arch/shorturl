import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import settings

_serializer = URLSafeTimedSerializer(settings.session_secret, salt="shorturl-session")

SESSION_COOKIE = "shorturl_session"


def verify_credentials(username: str, password: str) -> bool:
    if username != settings.admin_username:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), settings.admin_password_hash.encode("utf-8"))
    except ValueError:
        return False


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def make_session(username: str) -> str:
    return _serializer.dumps({"u": username})


def read_session(token: str | None) -> str | None:
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=settings.session_max_age_seconds)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("u")
