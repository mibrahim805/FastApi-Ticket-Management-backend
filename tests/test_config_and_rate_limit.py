import hashlib

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from app.api import rate_limit, registration
from app.api.permissions import require_role
from app.config import Settings


def _request(
    client_host: str = "203.0.113.10",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/login",
            "raw_path": b"/login",
            "query_string": b"",
            "root_path": "",
            "headers": headers or [],
            "client": (client_host, 50000),
            "server": ("testserver", 80),
        }
    )


def test_settings_load_required_values_from_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("MONGO_URI", raising=False)
    monkeypatch.delenv("MONGO_DB_NAME", raising=False)
    monkeypatch.delenv("ACCESS_TOKEN_EXPIRE_MINUTES", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SECRET_KEY=abcdefghijklmnopqrstuvwxyz-1234567890\n"
        "MONGO_URI=mongodb://database:27017\n"
        "MONGO_DB_NAME=tickets\n"
        "ACCESS_TOKEN_EXPIRE_MINUTES=30\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.secret_key.get_secret_value() == (
        "abcdefghijklmnopqrstuvwxyz-1234567890"
    )
    assert settings.mongo_uri == "mongodb://database:27017"
    assert settings.mongo_db_name == "tickets"
    assert settings.access_token_expire_minutes == 30


def test_settings_reject_short_jwt_secret():
    with pytest.raises(ValidationError):
        Settings(
            secret_key="too-short",
            mongo_uri="mongodb://database:27017",
            _env_file=None,
        )


def test_settings_require_jwt_secret_and_mongo_uri(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("MONGO_URI", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    missing_fields = {error["loc"][0] for error in exc_info.value.errors()}
    assert missing_fields == {"SECRET_KEY", "MONGO_URI"}


def test_reusable_role_dependency_allows_only_configured_roles():
    require_admin = require_role(
        "admin",
        "super_admin",
        detail="Only admins can continue",
    )

    user = {"role": "admin"}
    assert require_admin(user) is user

    with pytest.raises(HTTPException) as exc_info:
        require_admin({"role": "agent"})

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Only admins can continue"


def test_reusable_role_dependency_requires_at_least_one_role():
    with pytest.raises(ValueError):
        require_role()


def test_rate_limit_blocks_requests_after_configured_limit(rate_limits):
    request = _request()

    rate_limit.enforce_rate_limit(request, scope="login", max_requests=2)
    rate_limit.enforce_rate_limit(request, scope="login", max_requests=2)

    with pytest.raises(HTTPException) as exc_info:
        rate_limit.enforce_rate_limit(request, scope="login", max_requests=2)

    assert exc_info.value.status_code == 429
    assert int(exc_info.value.headers["Retry-After"]) >= 1
    assert len(rate_limits.documents) == 1
    assert rate_limits.documents[0]["count"] == 3
    assert rate_limits.documents[0]["scope"] == "login"
    assert (
        rate_limits.documents[0]["client_hash"]
        == hashlib.sha256(b"203.0.113.10").hexdigest()
    )
    assert "203.0.113.10" not in str(rate_limits.documents[0])


def test_rate_limit_can_be_disabled(monkeypatch, rate_limits):
    monkeypatch.setattr(rate_limit.settings, "rate_limit_enabled", False)

    rate_limit.enforce_rate_limit(
        _request(),
        scope="login",
        max_requests=1,
    )

    assert rate_limits.documents == []


@pytest.mark.parametrize(
    ("path", "dependency"),
    [
        ("/register_super_admin", rate_limit.registration_rate_limit),
        ("/register", rate_limit.registration_rate_limit),
        ("/login", rate_limit.login_rate_limit),
        ("/request-password-reset", rate_limit.password_reset_rate_limit),
        ("/reset-password", rate_limit.password_reset_rate_limit),
    ],
)
def test_public_auth_route_uses_rate_limit_dependency(path, dependency):
    route = next(route for route in registration.router.routes if route.path == path)

    assert dependency in [item.call for item in route.dependant.dependencies]
