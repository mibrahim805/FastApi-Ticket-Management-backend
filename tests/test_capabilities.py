import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.api import registration
from app.api.permissions import Capability, has_capability, require_capability
from app.models.registration import ChangeUserRole


@pytest.mark.parametrize(
    ("role", "capability", "allowed"),
    [
        ("lead", Capability.DELETE_TICKETS, True),
        ("member", Capability.UPDATE_TICKET, True),
        ("member", Capability.DELETE_TICKETS, False),
        ("reporter", Capability.VIEW_TICKET, True),
        ("reporter", Capability.VIEW_INTERNAL_COMMENTS, False),
        ("reporter", Capability.CREATE_PUBLIC_COMMENTS, True),
    ],
)
def test_project_role_capability_matrix(role, capability, allowed):
    assert has_capability({"role": role}, capability) is allowed


def test_super_admin_has_all_capabilities():
    assert all(
        has_capability({"role": "super_admin"}, capability) for capability in Capability
    )


def test_require_capability_rejects_missing_capability():
    with pytest.raises(HTTPException) as exc_info:
        require_capability(
            {"role": "reporter"},
            Capability.CREATE_INTERNAL_COMMENTS,
        )

    assert exc_info.value.status_code == 403


def test_super_admin_can_change_global_role(monkeypatch, fake_collection):
    user_id = ObjectId()
    users = fake_collection.__class__([{"_id": user_id, "role": "user"}])
    monkeypatch.setattr(registration, "user_collection", users)

    response = registration.change_user_role(
        str(user_id),
        ChangeUserRole(role="agent"),
        current_user={"_id": ObjectId(), "role": "super_admin"},
    )

    assert response["role"] == "agent"
    assert users.documents[0]["role"] == "agent"
