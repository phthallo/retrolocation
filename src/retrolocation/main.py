import contextlib
import io
import logging
import os
import secrets
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from itertools import takewhile
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from retro_sdk import Retro

TOKEN_FILE = Path(os.environ.get("RETRO_TOKEN_FILE", ".retro_refresh_token"))
ADMIN_SECRET = os.environ.get("RETROLOCATION_ADMIN_SECRET")
QUERY_SECRET = os.environ.get("RETROLOCATION_QUERY_SECRET")
RETRO_USER_ID = os.environ.get("RETRO_USER_ID")
CORS_ORIGINS = [o.strip() for o in os.environ.get("RETROLOCATION_CORS_ORIGINS", "").split(",") if o.strip()]
CACHE_SECONDS = 300
ERROR_BACKOFF_SECONDS = 60
DAY_SECONDS = 24 * 3600
WEEK_SECONDS = 7 * DAY_SECONDS
# caps on the count/weeks params; MAX_WEEKS bounds how many weeks one refresh fetches
MAX_COUNT = 50
MAX_WEEKS = 12
STATIC_DIR = Path(__file__).parent / "static"

if ADMIN_SECRET is not None and len(ADMIN_SECRET) < 32:
    raise RuntimeError("RETROLOCATION_ADMIN_SECRET must be at least 32 characters")
if not QUERY_SECRET or len(QUERY_SECRET) < 32:
    raise RuntimeError("RETROLOCATION_QUERY_SECRET must be at least 32 characters")
if not RETRO_USER_ID:
    raise RuntimeError("RETRO_USER_ID must be set")
if not os.environ.get("RETRO_TOKEN_KEY"):
    raise RuntimeError("RETRO_TOKEN_KEY must be set to a Fernet key")
FERNET = Fernet(os.environ["RETRO_TOKEN_KEY"])

logger = logging.getLogger(__name__)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET"],
    allow_headers=["X-Query-Secret"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_client: Retro | None = None
_client_mtime: float | None = None
_cache: dict[str, tuple[float, Any]] = {}
# (when, status code, detail) of the last failed refresh per key
_failed_at: dict[str, tuple[float, int, str]] = {}
_cache_locks = {"latest": threading.Lock(), "recent": threading.Lock()}
# redirect_stdout swaps the process-wide sys.stdout, so overlapping calls could leave it redirected
_verify_lock = threading.Lock()


def require_admin(x_admin_secret: str = Header(""), x_retro_user_id: str = Header("")):
    if not ADMIN_SECRET or not secrets.compare_digest(x_admin_secret.encode(), ADMIN_SECRET.encode()):
        raise HTTPException(403, "forbidden")
    # not a secret (anyone can look it up from a username), so this only catches the wrong account early
    if x_retro_user_id != RETRO_USER_ID:
        raise HTTPException(403, "wrong retro user id")


def require_query_secret(x_query_secret: str = Header("")):
    if not secrets.compare_digest(x_query_secret.encode(), QUERY_SECRET.encode()):
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


@app.post("/auth/check", dependencies=[Depends(require_admin)])
def check():
    return {"ok": True}


@app.post("/auth/send-code", dependencies=[Depends(require_admin)])
def send_code(body: SendCodeRequest):
    Retro().send_code(body.phone_number)
    return {"sent": True}


@app.post("/auth/verify", dependencies=[Depends(require_admin)])
def verify(body: VerifyRequest):
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
    _cache.clear()
    _failed_at.clear()
    return {"user_id": uid}


@app.get("/location", dependencies=[Depends(require_query_secret)])
def location(
    response: Response,
    count: int | None = Query(None, ge=1, le=MAX_COUNT),
    weeks: int | None = Query(None, ge=1, le=MAX_WEEKS),
    spread: bool = False,
):
    if count is None and weeks is None and not spread:
        result = cached("latest", latest_location)
        if result is None:
            raise HTTPException(404, "no posts with a location")
    elif count is None or weeks is None:
        raise HTTPException(422, "count and weeks must be passed together")
    else:
        result = recent_locations(count, weeks, spread)
    # private: shared caches must not serve a secret-gated response to other clients
    response.headers["Cache-Control"] = f"private, max-age={CACHE_SECONDS}"
    return result


@app.get("/public/recent")
def public_recent(response: Response):
    response.headers["Cache-Control"] = f"public, max-age={DAY_SECONDS}"
    # skip the last day so the public endpoint never shows where you are right now
    return recent_locations(count=3, weeks=4, spread=True, delay=DAY_SECONDS)


def recent_locations(count: int, weeks: int, spread: bool, delay: float = 0) -> dict:
    # one cached fetch of the widest window serves every count/weeks combination
    now = time.time()
    end = now - delay
    cutoff = now - weeks * WEEK_SECONDS
    photos = [
        p for p in takewhile(lambda p: p[0] >= cutoff, cached("recent", recent_located_photos))
        if p[0] <= end
    ]
    if spread:
        return {"locations": spread_out(photos, count, end, cutoff)}
    names = []
    for _, name in photos:
        if name not in names:
            names.append(name)
    return {"locations": names[:count]}


def spread_out(photos: list[tuple[float, str]], count: int, now: float, cutoff: float) -> list[str]:
    """Pick up to `count` names spread over the window in time and in place.

    The window is split into `count` equal time slots and each slot gets one name, preferring
    names that share no comma-separated part with an earlier pick ("Bondi, Sydney" and
    "Manly, Sydney" share "sydney", so they're taken to be close). Slots with no photos are
    filled from the rest of the window the same way. `photos` is newest first.
    """
    picked: dict[str, float] = {}
    seen_parts: set[str] = set()

    def parts(name: str) -> set[str]:
        return {p.strip().casefold() for p in name.split(",") if p.strip()}

    def pick_from(candidates: list[tuple[float, str]]) -> None:
        unpicked = [(when, name) for when, name in candidates if name not in picked]
        far = [(when, name) for when, name in unpicked if not parts(name) & seen_parts]
        if far or unpicked:
            when, name = (far or unpicked)[0]
            picked[name] = when
            seen_parts.update(parts(name))

    slot_seconds = (now - cutoff) / count
    slots: list[list[tuple[float, str]]] = [[] for _ in range(count)]
    for when, name in photos:
        slots[min(max(int((now - when) // slot_seconds), 0), count - 1)].append((when, name))
    for slot in slots:
        pick_from(slot)
    while len(picked) < count and len(picked) < len({name for _, name in photos}):
        pick_from(photos)
    return sorted(picked, key=picked.__getitem__, reverse=True)


def cached(key: str, fetch: Callable[[], Any]) -> Any:
    # public endpoint: cache so traffic doesn't turn into retro api calls on your account.
    # the lock means only one request refreshes each key at a time.
    with _cache_locks[key]:
        now = time.monotonic()
        entry = _cache.get(key)
        if entry is None or now - entry[0] > CACHE_SECONDS:
            failed = _failed_at.get(key)
            if failed and now - failed[0] < ERROR_BACKOFF_SECONDS:
                # a fresh exception each time: re-raising one instance grows its traceback forever
                raise HTTPException(failed[1], failed[2])
            try:
                entry = (now, fetch())
            except HTTPException as e:
                # back off here too: the wrong-account check calls retro on every attempt
                _failed_at[key] = (now, e.status_code, e.detail)
                raise
            except Exception:
                logger.exception("retro lookup failed for %s", key)
                _failed_at[key] = (now, 503, "retro is unavailable")
                if entry is None:
                    raise HTTPException(503, "retro is unavailable")
                # keep serving the last known result until the next refresh
                entry = (now, entry[1])
            _cache[key] = entry
        return entry[1]


def is_public_photo(media: dict) -> bool:
    # keyholders-only posts are private; memories carry an old location
    return (
        bool(media.get("locationName"))
        and not media.get("isKeyholdersOnly")
        and not media.get("memoryType")
    )


def taken_at(media: dict) -> float:
    return media.get("createdAt") or media.get("uploadedAt") or 0


def signed_in_client() -> tuple[Retro, str]:
    retro = get_client()
    uid = retro.get_current_user_id()
    if uid != RETRO_USER_ID:
        raise HTTPException(401, "signed in to the wrong retro account")
    return retro, uid


def latest_location() -> dict | None:
    retro, uid = signed_in_client()
    # week ids are "YYYY_WW", so a string sort is chronological
    for week_id in sorted(retro.profile_weeks(uid), reverse=True):
        located = [m for m in retro.get_week_media(uid, week_id) if is_public_photo(m)]
        if not located:
            continue
        latest = max(located, key=taken_at)
        return {"location": latest["locationName"]}
    return None


def recent_located_photos() -> list[tuple[float, str]]:
    """(taken_at, locationName) for public photos in the last MAX_WEEKS weeks, newest first."""
    retro, uid = signed_in_client()
    cutoff = time.time() - MAX_WEEKS * WEEK_SECONDS
    # retro's week numbering scheme is unknown, so take a lower bound a week early using the
    # calendar year (iso years roll over in late december and would skip "YYYY_52")
    start = datetime.fromtimestamp(cutoff - WEEK_SECONDS, UTC)
    first_week_id = f"{start.year}_{(start.timetuple().tm_yday - 1) // 7:02d}"
    photos = []
    for week_id in retro.profile_weeks(uid):
        if week_id < first_week_id:
            continue
        photos += [
            (taken_at(m), m["locationName"])
            for m in retro.get_week_media(uid, week_id)
            if is_public_photo(m) and taken_at(m) >= cutoff
        ]
    return sorted(photos, reverse=True)
