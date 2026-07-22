import hashlib
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, status
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.config import get_settings
from app.db.db import rate_limits_collection

settings = get_settings()


def _client_identifier(request: Request) -> str:
    client_host = request.client.host if request.client else "unknown"
    if settings.trust_proxy_headers:
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            client_host = forwarded_for.split(",", 1)[0].strip()
    return hashlib.sha256(client_host.encode("utf-8")).hexdigest()


def enforce_rate_limit(
    request: Request,
    *,
    scope: str,
    max_requests: int,
) -> None:
    """Apply a Mongo-backed fixed-window limit for one client and route scope."""
    if not settings.rate_limit_enabled:
        return

    now = datetime.now(timezone.utc)
    window_seconds = settings.rate_limit_window_seconds
    window_number = int(now.timestamp()) // window_seconds
    window_start = datetime.fromtimestamp(
        window_number * window_seconds,
        tz=timezone.utc,
    )
    window_end = window_start + timedelta(seconds=window_seconds)
    identifier = _client_identifier(request)
    bucket_id = f"{scope}:{identifier}:{window_number}"
    update = {
        "$inc": {"count": 1},
        "$setOnInsert": {
            "scope": scope,
            "client_hash": identifier,
            "window_start": window_start,
            "expires_at": window_end + timedelta(seconds=window_seconds),
        },
    }

    try:
        bucket = rate_limits_collection.find_one_and_update(
            {"_id": bucket_id},
            update,
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        bucket = rate_limits_collection.find_one_and_update(
            {"_id": bucket_id},
            {"$inc": {"count": 1}},
            return_document=ReturnDocument.AFTER,
        )

    if bucket and bucket.get("count", 0) > max_requests:
        retry_after = max(1, int((window_end - now).total_seconds()) + 1)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )


def rate_limit_dependency(scope: str, max_requests: int):
    def check_rate_limit(request: Request) -> None:
        enforce_rate_limit(
            request,
            scope=scope,
            max_requests=max_requests,
        )

    return check_rate_limit


login_rate_limit = rate_limit_dependency(
    "login",
    settings.login_rate_limit_requests,
)
registration_rate_limit = rate_limit_dependency(
    "registration",
    settings.registration_rate_limit_requests,
)
password_reset_rate_limit = rate_limit_dependency(
    "password_reset",
    settings.password_reset_rate_limit_requests,
)
