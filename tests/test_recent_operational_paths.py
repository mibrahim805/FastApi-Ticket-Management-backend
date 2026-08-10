import asyncio

import pytest
from bson import ObjectId
from fastapi import HTTPException
from pymongo.errors import OperationFailure

from app.api import permissions, project, status
from app.api.project import ComponentCreate
from app.db import db
from app.main import health_live
from app.models.project import ProjectMemberRole
from app.models.project_member import ProjectMemberRoleUpdate, ProjectMemberSuspension


def test_status_invalid_id_is_controlled_error(fake_collection, monkeypatch):
    monkeypatch.setattr(status, db.statuses_collection, fake_collection.__class__())
    with pytest.raises(HTTPException) as error:
        status.update_status(
            "not-an-object-id",
            status.StatusUpdate(name="Closed"),
            {"role": "super_admin"},
        )
    assert error.value.status_code == 400


def test_suspended_members_are_denied(fake_collection, monkeypatch):
    user_id, project_id = ObjectId(), ObjectId()
    members = fake_collection.__class__(
        [
            {
                "project_id": project_id,
                "user_id": user_id,
                "role": "member",
                "suspended": True,
            }
        ]
    )
    with pytest.raises(HTTPException) as error:
        permissions.require_project_membership(
            project_id, {"_id": user_id, "role": "user"}, members
        )
    assert error.value.status_code == 403


def test_index_reconciliation_recreates_conflicting_index():
    class Collection:
        def __init__(self):
            self.dropped = []

        def create_index(self, *_args, **_kwargs):
            if not self.dropped:
                raise OperationFailure("conflict", code=86)
            return "ok"

        def drop_index(self, name):
            self.dropped.append(name)

    collection = Collection()
    assert (
        db._ensure_index(collection, [("x", 1)], name="x_unique", unique=True) == "ok"
    )
    assert collection.dropped == ["x_unique"]


def test_liveness_endpoint():
    assert asyncio.run(health_live()) == {"status": "ok"}


def test_project_component_lifecycle_and_reference_guard(monkeypatch, fake_collection):
    project_id, component_id = ObjectId(), ObjectId()
    components = fake_collection.__class__()
    tickets = fake_collection.__class__()
    monkeypatch.setattr(project, "project_components_collection", components)
    monkeypatch.setattr(project, "ticket_collection", tickets)
    monkeypatch.setattr(
        project, "_get_project_or_404", lambda value: (project_id, {"_id": project_id})
    )
    monkeypatch.setattr(project, "_require_project_admin", lambda *_args: None)
    created = project.create_project_component(
        str(project_id), ComponentCreate(name=" UI "), {"_id": ObjectId()}
    )
    assert created["name"] == "UI"
    component_id = ObjectId(created["_id"])
    updated = project.update_project_component(
        str(project_id),
        str(component_id),
        ComponentCreate(name="Frontend"),
        {"_id": ObjectId()},
    )
    assert updated["name"] == "Frontend"
    monkeypatch.setattr(
        tickets, "find_one", lambda query: {"component_ids": [component_id]}
    )
    with pytest.raises(HTTPException) as error:
        project.delete_project_component(
            str(project_id), str(component_id), {"_id": ObjectId()}
        )
    assert error.value.status_code == 409


def test_project_member_management_lifecycle(monkeypatch, fake_collection):
    project_id, user_id = ObjectId(), ObjectId()
    members = fake_collection.__class__(
        [
            {
                "_id": ObjectId(),
                "project_id": project_id,
                "user_id": user_id,
                "role": "member",
                "suspended": False,
            }
        ]
    )
    monkeypatch.setattr(project, "project_members_collection", members)
    monkeypatch.setattr(
        project, "_get_project_or_404", lambda value: (project_id, {"_id": project_id})
    )
    monkeypatch.setattr(project, "_require_project_admin", lambda *_args: None)
    actor = {"_id": ObjectId(), "role": "admin"}
    result = project.update_project_member_role(
        str(project_id),
        str(user_id),
        ProjectMemberRoleUpdate(role=ProjectMemberRole.REPORTER),
        actor,
    )
    assert result["role"] == "reporter"
    result = project.suspend_project_member(
        str(project_id), str(user_id), ProjectMemberSuspension(suspended=True), actor
    )
    assert result["suspended"] is True
    members.documents[0]["suspended"] = False
    assert project.remove_project_member(str(project_id), str(user_id), actor)[
        "message"
    ]
