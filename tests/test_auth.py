import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.api import registration
from app.models.registration import Role, SuperAminRegister, UserLogin, UserRegister


def test_register_super_admin_inserts_first_admin(monkeypatch, fake_collection):
    monkeypatch.setattr(registration, "user_collection", fake_collection)
    monkeypatch.setattr(registration, "hash_password", lambda value: f"hashed:{value}")

    payload = SuperAminRegister(
        name="Ibrahim", email="admin@example.com", password="secret"
    )

    response = registration.register_super_admin(payload)

    assert response == {"message": "Super admin registered successfully"}
    assert fake_collection.documents[0]["role"] == "super_admin"
    assert fake_collection.documents[0]["hashed_password"] == "hashed:secret"


def test_register_blocks_agent_from_creating_admin_roles(monkeypatch, fake_collection):
    monkeypatch.setattr(registration, "user_collection", fake_collection)
    monkeypatch.setattr(registration, "hash_password", lambda value: f"hashed:{value}")

    payload = UserRegister(
        name="Agent",
        email="agent@example.com",
        password="secret",
        role=Role.ADMIN.value,
    )

    with pytest.raises(HTTPException) as exc_info:
        registration.register(payload, current_user={"role": Role.AGENT.value})

    assert exc_info.value.status_code == 403


def test_login_returns_token_for_valid_user(monkeypatch, fake_collection):
    user_id = ObjectId()
    fake_collection.documents.append(
        {
            "_id": user_id,
            "email": "user@example.com",
            "hashed_password": "hashed",
            "is_active": True,
        }
    )
    monkeypatch.setattr(registration, "user_collection", fake_collection)
    monkeypatch.setattr(
        registration, "verify_password", lambda raw, hashed: raw == "secret"
    )
    monkeypatch.setattr(
        registration, "create_access_token", lambda payload: f"token:{payload['sub']}"
    )

    response = registration.login(
        UserLogin(email="user@example.com", password="secret")
    )

    assert response == {"access_token": f"token:{user_id}", "token_type": "bearer"}


def test_login_rejects_inactive_user(monkeypatch, fake_collection):
    fake_collection.documents.append(
        {
            "_id": ObjectId(),
            "email": "user@example.com",
            "hashed_password": "hashed",
            "is_active": False,
        }
    )
    monkeypatch.setattr(registration, "user_collection", fake_collection)
    monkeypatch.setattr(registration, "verify_password", lambda raw, hashed: True)

    with pytest.raises(HTTPException) as exc_info:
        registration.login(UserLogin(email="user@example.com", password="secret"))

    assert exc_info.value.status_code == 401
