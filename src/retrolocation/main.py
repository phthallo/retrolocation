import contextlib
import io
import logging
import os
import secrets
import threading
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from retro_sdk import Retro

TOKEN_FILE = Path(os.environ.get("RETRO_TOKEN_FILE", ".retro_refresh_token"))
ADMIN_SECRET = os.environ.get("RETROLOCATION_ADMIN_SECRET")
RETRO_USER_ID = os.environ.get("RETRO_USER_ID")
CACHE_SECONDS = 300
ERROR_BACKOFF_SECONDS = 60
STATIC_DIR = Path(__file__).parent / "static"

if ADMIN_SECRET is not None and len(ADMIN_SECRET) < 32:
    raise RuntimeError("RETROLOCATION_ADMIN_SECRET must be at least 32 characters")
if not RETRO_USER_ID:
    raise RuntimeError("RETRO_USER_ID must be set")
if not os.environ.get("RETRO_TOKEN_KEY"):
    raise RuntimeError("RETRO_TOKEN_KEY must be set to a Fernet key")
FERNET = Fernet(os.environ["RETRO_TOKEN_KEY"])

logger = logging.getLogger(__name__)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["https://phthallo.com"], allow_methods=["GET"])
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_client: Retro | None = None
_client_mtime: float | None = None
_cache: tuple[float, dict | None] | None = None
_failed_at = 0.0
_location_lock = threading.Lock()
# redirect_stdout swaps the process-wide sys.stdout, so overlapping calls could leave it redirected
_verify_lock = threading.Lock()


def require_admin(x_admin_secret: str = Header("")):
    if not ADMIN_SECRET or not secrets.compare_digest(x_admin_secret.encode(), ADMIN_SECRET.encode()):
        raise HTTPException(403, "forbidden")


class SendCodeRequest(BaseModel):
    phone_number: str


class VerifyRequest(BaseModel):
    phone_number: str
    code: str


def get_client() -> Retro:
    global _client, _client_mtime
    try:
        mtime = TOKEN_FILE.stat().st_mtime
    except FileNotFoundError:
        raise HTTPException(401, "not signed in")
    # reload when another worker or process has written a new token
    if _client is None or mtime != _client_mtime:
        try:
            token = FERNET.decrypt(TOKEN_FILE.read_bytes()).decode()
        except InvalidToken:
            logger.error("can't decrypt %s: RETRO_TOKEN_KEY changed or the file is plaintext", TOKEN_FILE)
            raise HTTPException(401, "not signed in")
        _client = Retro(refresh_token=token)
        _client_mtime = mtime
    return _client


def save_refresh_token(token: str) -> None:
    tmp = TOKEN_FILE.with_name(TOKEN_FILE.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(FERNET.encrypt(token.encode()))
    os.replace(tmp, TOKEN_FILE)


@app.get("/", include_in_schema=False)
def sign_in_page():
    return FileResponse(STATIC_DIR / "signin.html")


@app.post("/auth/send-code", dependencies=[Depends(require_admin)])
def send_code(body: SendCodeRequest):
    Retro().send_code(body.phone_number)
    return {"sent": True}


@app.post("/auth/verify", dependencies=[Depends(require_admin)])
def verify(body: VerifyRequest):
    global _cache
    retro = Retro()
    # the sdk prints the full token response, refresh token included
    with _verify_lock, contextlib.redirect_stdout(io.StringIO()):
        ok = retro.verify_code(body.code, phone_number=body.phone_number)
    if not ok:
        raise HTTPException(401, "invalid code")
    # verify_code leaves a custom token in auth_token, so read the uid from a refreshed client
    uid = Retro(refresh_token=retro.refresh_token).get_current_user_id()
    if uid != RETRO_USER_ID:
        raise HTTPException(403, f"signed in as {uid}, which isn't RETRO_USER_ID")
    save_refresh_token(retro.refresh_token)
    _cache = None
    return {"user_id": uid}


@app.get("/location")
def location():
    global _cache, _failed_at
    # public endpoint: cache so traffic doesn't turn into retro api calls on your account.
    # the lock means only one request refreshes at a time.
    with _location_lock:
        now = time.monotonic()
        if _cache is None or now - _cache[0] > CACHE_SECONDS:
            if _cache is None and now - _failed_at < ERROR_BACKOFF_SECONDS:
                raise HTTPException(503, "retro is unavailable")
            try:
                _cache = (now, latest_location())
            except HTTPException:
                raise
            except Exception:
                logger.exception("retro location lookup failed")
                _failed_at = now
                if _cache is None:
                    raise HTTPException(503, "retro is unavailable")
                # keep serving the last known location until the next refresh
                _cache = (now, _cache[1])
        result = _cache[1]
    if result is None:
        raise HTTPException(404, "no posts with a location")
    return result


def is_public_photo(media: dict) -> bool:
    # keyholders-only posts are private; memories carry an old location
    return (
        bool(media.get("locationName"))
        and not media.get("isKeyholdersOnly")
        and not media.get("memoryType")
    )


def latest_location() -> dict | None:
    retro = get_client()
    uid = retro.get_current_user_id()
    if uid != RETRO_USER_ID:
        raise HTTPException(401, "signed in to the wrong retro account")
    # week ids are "YYYY_WW", so a string sort is chronological
    for week_id in sorted(retro.profile_weeks(uid), reverse=True):
        located = [m for m in retro.get_week_media(uid, week_id) if is_public_photo(m)]
        if not located:
            continue
        latest = max(located, key=lambda m: m.get("createdAt") or m.get("uploadedAt") or 0)
        return {"location": latest["locationName"]}
    return None
