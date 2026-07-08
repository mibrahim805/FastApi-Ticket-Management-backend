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


def test_register_admin_or_agent_blocks_agent(monkeypatch, fake_collection):
    monkeypatch.setattr(registration, "user_collection", fake_collection)

    payload = UserRegister(
        name="Agent",
        email="agent@example.com",
        password="secret",
        role=Role.ADMIN.value,
    )

    with pytest.raises(HTTPException) as exc_info:
        registration.register_admin_or_agent(
            payload,
            current_user={"role": Role.AGENT.value},
        )

    assert exc_info.value.status_code == 403


def test_register_rejects_second_super_admin(monkeypatch, fake_collection):
    fake_collection.documents.append({"role": "super_admin"})
    monkeypatch.setattr(registration, "user_collection", fake_collection)

    response = registration.register_super_admin(
        SuperAminRegister(
            name="Second",
            email="second@example.com",
            password="secret",
        )
    )

    assert response == {"message": "An super admin already exists."}
    assert len(fake_collection.documents) == 1


def test_register_rejects_duplicate_email(monkeypatch, fake_collection):
    fake_collection.documents.append({"email": "taken@example.com"})
    monkeypatch.setattr(registration, "user_collection", fake_collection)

    payload = UserRegister(
        name="User",
        email="taken@example.com",
        password="secret",
        role=Role.USER.value,
    )

    with pytest.raises(HTTPException) as exc_info:
        registration.register(payload)

    assert exc_info.value.status_code == 400


def test_register_admin_or_agent_blocks_customer(monkeypatch, fake_collection):
    monkeypatch.setattr(registration, "user_collection", fake_collection)

    payload = UserRegister(
        name="Admin",
        email="admin@example.com",
        password="secret",
        role=Role.ADMIN.value,
    )

    with pytest.raises(HTTPException) as exc_info:
        registration.register_admin_or_agent(
            payload,
            current_user={"role": Role.CUSTOMER.value},
        )

    assert exc_info.value.status_code == 403


def test_register_succeeds(monkeypatch, fake_collection):
    monkeypatch.setattr(registration, "user_collection", fake_collection)
    monkeypatch.setattr(
        registration,
        "hash_password",
        lambda value: f"hashed:{value}",
    )

    payload = UserRegister(
        name="User",
        email="user@example.com",
        password="secret",
        role=Role.USER.value,
    )

    response = registration.register(payload)

    assert response == {"message": "User registered successfully"}

    assert fake_collection.documents[0]["role"] == Role.USER.value
    assert fake_collection.documents[0]["hashed_password"] == "hashed:secret"


def test_register_rejects_invalid_role(
    monkeypatch,
    fake_collection,
):
    monkeypatch.setattr(registration, "user_collection", fake_collection)

    payload = UserRegister(
        name="User",
        email="user@example.com",
        password="secret",
        role="invalid-role",
    )

    with pytest.raises(HTTPException) as exc_info:
        registration.register(payload)

    assert exc_info.value.status_code == 400


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


def test_login_rejects_wrong_password(monkeypatch, fake_collection):
    fake_collection.documents.append(
        {
            "_id": ObjectId(),
            "email": "user@example.com",
            "hashed_password": "hashed",
            "is_active": True,
        }
    )
    monkeypatch.setattr(registration, "user_collection", fake_collection)
    monkeypatch.setattr(registration, "verify_password", lambda raw, hashed: False)

    with pytest.raises(HTTPException) as exc_info:
        registration.login(UserLogin(email="user@example.com", password="wrong"))

    assert exc_info.value.status_code == 401


def test_login_rejects_unknown_email(monkeypatch, fake_collection):
    monkeypatch.setattr(registration, "user_collection", fake_collection)

    with pytest.raises(HTTPException) as exc_info:
        registration.login(UserLogin(email="missing@example.com", password="secret"))

    assert exc_info.value.status_code == 401


def test_register_admin_or_agent_success(
    monkeypatch,
    fake_collection,
):
    monkeypatch.setattr(registration, "user_collection", fake_collection)
    monkeypatch.setattr(
        registration,
        "hash_password",
        lambda value: f"hashed:{value}",
    )

    payload = UserRegister(
        name="Admin",
        email="admin@example.com",
        password="secret",
        role=Role.ADMIN.value,
    )

    response = registration.register_admin_or_agent(
        payload,
        current_user={"role": Role.SUPER_ADMIN.value},
    )

    assert response == {"message": "Admin registered successfully"}

    assert fake_collection.documents[0]["role"] == Role.ADMIN.value


def test_register_admin_or_agent_duplicate_email(
    monkeypatch,
    fake_collection,
):
    fake_collection.documents.append({"email": "admin@example.com"})

    monkeypatch.setattr(
        registration,
        "user_collection",
        fake_collection,
    )

    payload = UserRegister(
        name="Admin",
        email="admin@example.com",
        password="secret",
        role=Role.ADMIN.value,
    )

    with pytest.raises(HTTPException) as exc_info:
        registration.register_admin_or_agent(
            payload,
            current_user={"role": Role.SUPER_ADMIN.value},
        )

    assert exc_info.value.status_code == 400


def test_register_admin_or_agent_invalid_role(
    monkeypatch,
    fake_collection,
):
    monkeypatch.setattr(
        registration,
        "user_collection",
        fake_collection,
    )

    payload = UserRegister(
        name="User",
        email="user@example.com",
        password="secret",
        role=Role.USER.value,
    )

    with pytest.raises(HTTPException) as exc_info:
        registration.register_admin_or_agent(
            payload,
            current_user={"role": Role.SUPER_ADMIN.value},
        )

    assert exc_info.value.status_code == 400
