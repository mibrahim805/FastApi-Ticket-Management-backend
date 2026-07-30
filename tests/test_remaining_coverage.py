import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.api import activity, permissions, project, registration
from app.models.permission import Permission
from app.models.project import ProjectRolePermissionUpdate
from app.models.registration import ChangeGlobalRole, GlobalRole


def test_project_activity_endpoint_serializes_documents(monkeypatch):
    project_id = ObjectId()
    monkeypatch.setattr(
        activity,
        "projects_collection",
        type(
            "C",
            (),
            {
                "find_one": lambda self, query: {"_id": project_id},
            },
        )(),
    )
    monkeypatch.setattr(
        activity,
        "project_activity_collection",
        type(
            "C",
            (),
            {
                "find": lambda self, query: type(
                    "Cursor",
                    (),
                    {
                        "sort": lambda self, field, order: [{"changed_at": None}],
                    },
                )(),
            },
        )(),
    )
    monkeypatch.setattr(activity, "require_project_membership", lambda *a, **k: {})

    response = activity.get_project_activity(str(project_id), {"_id": ObjectId()})
    assert response.body

    monkeypatch.setattr(activity.projects_collection, "find_one", lambda query: None)
    with pytest.raises(HTTPException):
        activity.get_project_activity(str(project_id), {"_id": ObjectId()})


def test_permission_and_capability_edge_branches(monkeypatch):
    assert permissions.role_has_permission("invalid", Permission.VIEW_PROJECT) is False
    assert permissions.role_has_permission("member", Permission.VIEW_PROJECT, None)
    assert permissions.effective_capabilities(None) == frozenset()
    assert permissions.has_capability({}, "invalid") is False
    assert permissions.can_assign_ticket({}, {}, {}, "user") is False

    class Overrides:
        def find_one(self, query):
            return {"removed": ["view_project"]}

    monkeypatch.setattr(
        permissions, "project_permission_overrides_collection", Overrides()
    )
    project_id = str(ObjectId())
    assert not permissions.role_has_permission(
        "member", Permission.VIEW_PROJECT, project_id
    )

    class Added:
        def find_one(self, query):
            return {"added": ["manage_project"]}

    monkeypatch.setattr(permissions, "project_permission_overrides_collection", Added())
    assert permissions.role_has_permission(
        "member", Permission.MANAGE_PROJECT, project_id
    )
    assert permissions.role_has_permission(
        "member", Permission.CREATE_TICKET, project_id
    )

    class NoOverride:
        def find_one(self, query):
            return None

    monkeypatch.setattr(
        permissions, "project_permission_overrides_collection", NoOverride()
    )
    assert permissions.role_has_permission(
        "member", Permission.CREATE_TICKET, project_id
    )
    assert permissions.effective_capabilities(
        {
            "role": "member",
            "capability_revocations": ["bad"],
            "capability_grants": ["bad"],
        }
    )

    class Memberships:
        def find_one(self, query):
            return {"role": "member"}

    with pytest.raises(HTTPException):
        permissions.require_project_permission(
            project_id,
            {"_id": ObjectId()},
            Memberships(),
            Permission.DELETE_ANY_COMMENT,
        )
    assert (
        permissions.require_project_permission(
            project_id, {"_id": ObjectId()}, Memberships(), Permission.VIEW_PROJECT
        )["role"]
        == "member"
    )
    monkeypatch.setattr(
        permissions,
        "require_ticket_membership",
        lambda *a, **k: Memberships().find_one({}),
    )
    assert (
        permissions.require_ticket_capability(
            {}, {}, None, permissions.Capability.VIEW_PROJECT
        )["role"]
        == "member"
    )


def test_project_permission_endpoints_and_validation(monkeypatch):
    project_id = ObjectId()
    project_doc = {"_id": project_id, "permission_overrides": {}}
    monkeypatch.setattr(
        project, "_get_project_or_404", lambda value: (project_id, project_doc)
    )
    monkeypatch.setattr(
        project, "require_project_membership", lambda *a, **k: {"role": "super_admin"}
    )
    monkeypatch.setattr(
        project, "_serialize_project_permissions", lambda value: {"ok": True}
    )
    user = {"_id": ObjectId()}
    assert project.get_project_permissions(str(project_id), user) == {"ok": True}
    assert (
        project.get_project_capabilities(str(project_id), user)["role"] == "super_admin"
    )
    assert (
        len(project.get_my_project_permissions(str(project_id), user)["permissions"])
        > 0
    )

    monkeypatch.setattr(project, "_require_project_admin", lambda *a, **k: None)
    with pytest.raises(HTTPException):
        project.update_project_permissions(
            str(project_id),
            ProjectRolePermissionUpdate(role="member", grants={"unknown"}),
            user,
        )
    with pytest.raises(HTTPException):
        project.update_project_permissions(
            str(project_id),
            ProjectRolePermissionUpdate(
                role="member", grants={"view_project"}, revocations={"view_project"}
            ),
            user,
        )


def test_change_user_role_rejection_branches(monkeypatch):
    current = {"role": GlobalRole.SUPER_ADMIN.value}
    payload = ChangeGlobalRole(role=GlobalRole.ADMIN)
    with pytest.raises(HTTPException):
        registration.change_user_role("bad-id", payload, current)

    class Users:
        def find_one(self, query):
            return None

    monkeypatch.setattr(registration, "user_collection", Users())
    with pytest.raises(HTTPException):
        registration.change_user_role(str(ObjectId()), payload, current)

    class SpecialUsers:
        def __init__(self, role):
            self.role = role

        def find_one(self, query):
            return {"_id": query["_id"], "role": self.role}

        def update_one(self, *args):
            return None

    monkeypatch.setattr(registration, "user_collection", SpecialUsers("super_admin"))
    with pytest.raises(HTTPException):
        registration.change_user_role(str(ObjectId()), payload, current)
    monkeypatch.setattr(registration, "user_collection", SpecialUsers("agent"))
    with pytest.raises(HTTPException):
        registration.change_user_role(
            str(ObjectId()), ChangeGlobalRole(role=GlobalRole.SUPER_ADMIN), current
        )
    registration.change_user_role(str(ObjectId()), payload, current)
