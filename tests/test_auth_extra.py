import hashlib
from datetime import datetime, timedelta

import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.api import registration
from app.models.registration import (
    ChangePasswordRequest,
    PasswordResetRequest,
    ResetPasswordRequest,
)


def test_create_password_reset_token_is_consumed_once(fake_collection):
    reset_tokens = fake_collection.__class__()
    user = {"_id": ObjectId(), "email": "u@example.com"}

    # create token
    token = registration.create_password_reset_token(reset_tokens, user)
    assert isinstance(token, str)

    # token is stored
    record = reset_tokens.find_one(
        {"token_hash": hashlib.sha256(token.encode()).hexdigest()}
    )
    assert record is not None
    assert "token" not in record
    assert record["user_id"] == str(user["_id"])

    # valid token is returned once and atomically marked used
    consumed = registration.consume_password_reset_token(reset_tokens, token)
    assert consumed is not None
    assert consumed["user_id"] == str(user["_id"])
    assert registration.consume_password_reset_token(reset_tokens, token) is None


def test_password_reset_token_can_only_be_consumed_once(fake_collection):
    reset_tokens = fake_collection.__class__()
    user = {"_id": ObjectId(), "email": "u@example.com"}
    token = registration.create_password_reset_token(reset_tokens, user)

    assert registration.consume_password_reset_token(reset_tokens, token) is not None
    assert registration.consume_password_reset_token(reset_tokens, token) is None

    # expired token returns None
    reset_tokens.documents[0]["used"] = False
    reset_tokens.documents[0]["expires_at"] = datetime.utcnow() - timedelta(minutes=1)
    assert registration.consume_password_reset_token(reset_tokens, token) is None


def test_password_reset_token_rejects_invalid_token(fake_collection):
    assert registration.consume_password_reset_token(fake_collection, "invalid") is None


def test_request_password_reset_messages(monkeypatch, fake_collection):
    users = fake_collection.__class__()
    resets = fake_collection.__class__()
    monkeypatch.setattr(registration, "user_collection", users)
    monkeypatch.setattr(registration, "reset_tokens_collection", resets)

    # missing user returns generic message
    resp = registration.request_password_reset(
        PasswordResetRequest(email="missing@example.com")
    )
    assert "reset link has been sent" in resp["message"]

    # existing user also returns generic message and inserts token
    users.insert_one({"_id": ObjectId(), "email": "found@example.com"})
    resp2 = registration.request_password_reset(
        PasswordResetRequest(email="found@example.com")
    )
    assert "reset link has been sent" in resp2["message"]
    assert len(resets.inserted_documents) == 1


def test_reset_password_invalid_token(monkeypatch, fake_collection):
    resets = fake_collection.__class__()
    monkeypatch.setattr(registration, "reset_tokens_collection", resets)

    with pytest.raises(HTTPException) as exc:
        registration.reset_password(
            ResetPasswordRequest(token="nope", new_password="new-password")
        )
    assert exc.value.status_code == 400


def test_reset_password_success(monkeypatch, fake_collection):
    uid = ObjectId()

    users = fake_collection.__class__(
        [
            {
                "_id": uid,
                "email": "u@example.com",
                "hashed_password": "oldhash",
            }
        ]
    )

    resets = fake_collection.__class__(
        [
            {
                "token_hash": hashlib.sha256(b"tok").hexdigest(),
                "user_id": str(uid),
                "email": "u@example.com",
                "type": "password_reset",
                "used": False,
                "expires_at": datetime.utcnow() + timedelta(minutes=5),
            }
        ]
    )

    monkeypatch.setattr(registration, "user_collection", users)
    monkeypatch.setattr(registration, "reset_tokens_collection", resets)
    monkeypatch.setattr(
        registration,
        "hash_password",
        lambda password: f"hashed:{password}",
    )

    response = registration.reset_password(
        ResetPasswordRequest(token="tok", new_password="new-password")
    )

    assert response == {"message": "Password reset successful"}

    # Password updated
    updated_user = users.find_one({"_id": uid})
    assert updated_user["hashed_password"] == "hashed:new-password"

    # Token marked as used
    updated_token = resets.find_one({"token_hash": hashlib.sha256(b"tok").hexdigest()})
    assert updated_token["used"] is True


def test_activate_or_deactivate_and_updates(monkeypatch, fake_collection):
    users = fake_collection.__class__()
    monkeypatch.setattr(registration, "user_collection", users)

    # unauthorized caller
    with pytest.raises(HTTPException) as exc:
        registration.activate_or_deactivate(
            str(ObjectId()), current_user={"role": "agent"}
        )
    assert exc.value.status_code == 403

    # missing target user
    with pytest.raises(HTTPException) as exc2:
        registration.activate_or_deactivate(
            str(ObjectId()), current_user={"role": "super_admin"}
        )
    assert exc2.value.status_code == 404

    # cannot act on super_admin
    uid = ObjectId()
    users.insert_one({"_id": uid, "role": "super_admin", "is_active": True})
    with pytest.raises(HTTPException) as exc3:
        registration.activate_or_deactivate(
            str(uid), current_user={"role": "super_admin"}
        )
    assert exc3.value.status_code == 403

    # success toggle
    uid2 = ObjectId()
    users.insert_one({"_id": uid2, "is_active": True})
    resp = registration.activate_or_deactivate(
        str(uid2), current_user={"role": "admin"}
    )
    assert resp == {"message": "User deactivated successfully"}
    # toggled
    u2 = users.find_one({"_id": uid2})
    assert u2["is_active"] is False


def test_update_yourself_and_change_password(monkeypatch, fake_collection):
    uid = ObjectId()
    users = fake_collection.__class__(
        [{"_id": uid, "name": "Old", "hashed_password": "oldhash"}]
    )
    monkeypatch.setattr(registration, "user_collection", users)
    monkeypatch.setattr(registration, "hash_password", lambda p: f"hashed:{p}")
    monkeypatch.setattr(
        registration, "verify_password", lambda old, h: old == "old-password"
    )

    # update_yourself missing db_user
    with pytest.raises(HTTPException) as exc:
        registration.update_yourself({}, current_user={"_id": ObjectId()})
    assert exc.value.status_code == 404

    # successful update_yourself
    from app.models.registration import UserUpdate

    resp = registration.update_yourself(
        UserUpdate(name="New", password="update-password", is_active=True),
        current_user={"_id": uid},
    )
    assert resp == {"message": "User updated successfully"}
    updated_user = users.find_one({"_id": uid})
    assert updated_user["name"] == "New"
    assert updated_user["hashed_password"] == "hashed:update-password"
    assert "password" not in updated_user

    # change_password: missing db_user
    with pytest.raises(HTTPException) as exc2:
        registration.change_password(
            ChangePasswordRequest(old_password="a", new_password="new-password"),
            current_user={"_id": ObjectId()},
        )
    assert exc2.value.status_code == 404

    # wrong old password
    with pytest.raises(HTTPException) as exc3:
        registration.change_password(
            ChangePasswordRequest(old_password="wrong", new_password="new-password"),
            current_user={"_id": uid},
        )
    assert exc3.value.status_code == 401

    # same new password
    with pytest.raises(HTTPException) as exc4:
        registration.change_password(
            ChangePasswordRequest(
                old_password="old-password", new_password="old-password"
            ),
            current_user={"_id": uid},
        )
    assert exc4.value.status_code == 400

    # successful change
    resp2 = registration.change_password(
        ChangePasswordRequest(old_password="old-password", new_password="new-password"),
        current_user={"_id": uid},
    )
    assert resp2 == {"message": "Password changed successfully"}
    assert users.find_one({"_id": uid})["hashed_password"] == "hashed:new-password"
