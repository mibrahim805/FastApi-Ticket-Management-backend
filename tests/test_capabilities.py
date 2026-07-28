import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.api import project, registration, ticket
from app.api.permissions import (
    ROLE_CAPABILITIES,
    Capability,
    can_assign_ticket,
    can_upload_attachment,
    get_accessible_project_ids,
    has_capability,
    require_capability,
    transition_guard_error,
)
from app.models.project import ProjectRolePermissionUpdate
from app.models.registration import ChangeGlobalRole


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


def test_project_override_can_revoke_a_default_capability():
    membership = {
        "role": "member",
        "capability_revocations": [Capability.UPDATE_TICKET.value],
    }
    assert not has_capability(membership, Capability.UPDATE_TICKET)


def test_super_admin_has_all_capabilities():
    assert all(
        has_capability({"role": "super_admin"}, capability) for capability in Capability
    )


def test_attachment_policy_is_explicit_for_each_role():
    assert Capability.UPLOAD_ATTACHMENTS in ROLE_CAPABILITIES["lead"]
    assert Capability.UPLOAD_ATTACHMENTS not in ROLE_CAPABILITIES["member"]
    assert Capability.UPLOAD_ATTACHMENTS not in ROLE_CAPABILITIES["reporter"]


def test_reporter_attachment_upload_requires_ticket_actor_exception():
    ticket = {"userId": "reporter", "assigned_agent": None}
    reporter = {"_id": "reporter", "role": "customer"}
    assert can_upload_attachment(ticket, reporter, {"role": "reporter"})
    assert not can_upload_attachment(
        ticket,
        {"_id": "other", "role": "customer"},
        {"role": "reporter"},
    )


def test_assignment_policy_centralizes_lead_and_member_rules():
    ticket = {"assigned_agent": None}
    assert can_assign_ticket(
        ticket,
        {"_id": "member"},
        {"role": "member"},
        "member",
    )
    assert not can_assign_ticket(
        ticket,
        {"_id": "member"},
        {"role": "member"},
        "other",
    )
    assert can_assign_ticket(
        {"assigned_agent": "someone"},
        {"_id": "lead"},
        {"role": "lead"},
        "other",
    )


def test_transition_role_guard_is_centralized():
    transition = {"guard": {"allowed_roles": ["lead"]}}
    assert transition_guard_error(transition, {}, {"role": "member"}) == (
        403,
        "Your project role cannot use this transition",
    )
    assert transition_guard_error(transition, {}, {"role": "lead"}) is None


def test_project_can_grant_reporter_attachment_upload(
    monkeypatch,
    fake_collection,
    project_members,
    project_activities,
):
    project_a_id = ObjectId()
    project_b_id = ObjectId()
    lead_id = ObjectId()
    reporter_id = ObjectId()
    projects = fake_collection.__class__(
        [
            {"_id": project_a_id, "permission_overrides": {}},
            {"_id": project_b_id, "permission_overrides": {}},
        ]
    )
    monkeypatch.setattr(project, "projects_collection", projects)
    for project_id in (project_a_id, project_b_id):
        project_members.insert_one(
            {"project_id": project_id, "user_id": lead_id, "role": "lead"}
        )
        project_members.insert_one(
            {"project_id": project_id, "user_id": reporter_id, "role": "reporter"}
        )

    project.update_project_permissions(
        str(project_b_id),
        ProjectRolePermissionUpdate(
            role="reporter",
            grants={Capability.UPLOAD_ATTACHMENTS.value},
        ),
        current_user={"_id": lead_id, "role": "user"},
    )

    project_a_reporter = project_members.find_one(
        {"project_id": project_a_id, "user_id": reporter_id}
    )
    project_b_reporter = project_members.find_one(
        {"project_id": project_b_id, "user_id": reporter_id}
    )
    assert not has_capability(
        project_a_reporter,
        Capability.UPLOAD_ATTACHMENTS,
    )
    assert has_capability(
        project_b_reporter,
        Capability.UPLOAD_ATTACHMENTS,
    )
    unrelated_ticket = {"userId": "someone-else", "assigned_agent": None}
    reporter = {"_id": reporter_id, "role": "customer"}
    assert not can_upload_attachment(
        unrelated_ticket,
        reporter,
        project_a_reporter,
    )
    assert can_upload_attachment(
        unrelated_ticket,
        reporter,
        project_b_reporter,
    )
    assert project_activities.documents[-1]["action"] == ("project_permissions_updated")
    assert project_activities.documents[-1]["changes"][0]["role"] == "reporter"


def test_project_revocation_removes_project_from_cross_project_views(
    fake_collection,
):
    visible_project_id = ObjectId()
    hidden_project_id = ObjectId()
    user_id = ObjectId()
    memberships = fake_collection.__class__(
        [
            {
                "project_id": visible_project_id,
                "user_id": user_id,
                "role": "reporter",
            },
            {
                "project_id": hidden_project_id,
                "user_id": user_id,
                "role": "reporter",
                "capability_revocations": [Capability.VIEW_PROJECT.value],
            },
        ]
    )

    assert get_accessible_project_ids(
        {"_id": user_id, "role": "user"},
        memberships,
        fake_collection.__class__(
            [{"_id": visible_project_id}, {"_id": hidden_project_id}]
        ),
    ) == [str(visible_project_id)]


def test_project_permission_override_rejects_manage_project(
    monkeypatch,
    fake_collection,
    project_members,
):
    project_id = ObjectId()
    lead_id = ObjectId()
    monkeypatch.setattr(
        project,
        "projects_collection",
        fake_collection.__class__([{"_id": project_id, "permission_overrides": {}}]),
    )
    project_members.insert_one(
        {"project_id": project_id, "user_id": lead_id, "role": "lead"}
    )

    with pytest.raises(HTTPException) as exc_info:
        project.update_project_permissions(
            str(project_id),
            ProjectRolePermissionUpdate(
                role="reporter",
                grants={Capability.MANAGE_PROJECT.value},
            ),
            current_user={"_id": lead_id, "role": "user"},
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "manage_project cannot be overridden"


def test_require_capability_rejects_missing_capability():
    with pytest.raises(HTTPException) as exc_info:
        require_capability(
            {"role": "reporter"},
            Capability.CREATE_INTERNAL_COMMENTS,
        )

    assert exc_info.value.status_code == 403


def test_project_capabilities_endpoint_returns_current_users_effective_access(
    monkeypatch, fake_collection, project_members
):
    project_id = ObjectId()
    user_id = ObjectId()
    monkeypatch.setattr(
        project,
        "projects_collection",
        fake_collection.__class__([{"_id": project_id}]),
    )
    project_members.insert_one(
        {
            "project_id": project_id,
            "user_id": user_id,
            "role": "reporter",
            "capability_grants": [Capability.UPDATE_TICKET.value],
        }
    )

    response = project.get_project_capabilities(
        str(project_id), current_user={"_id": user_id, "role": "customer"}
    )

    assert response["role"] == "reporter"
    assert Capability.UPDATE_TICKET.value in response["capabilities"]
    assert response["can"][Capability.UPDATE_TICKET.value] is True
    assert response["can"][Capability.DELETE_TICKETS.value] is False


def test_ticket_capabilities_endpoint_includes_ticket_actor_permissions(
    monkeypatch, fake_collection, project_members
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    user_id = ObjectId()
    monkeypatch.setattr(
        ticket,
        "projects_collection",
        fake_collection.__class__([{"_id": project_id}]),
    )
    monkeypatch.setattr(
        ticket,
        "ticket_collection",
        fake_collection.__class__(
            [
                {
                    "_id": ticket_id,
                    "project_id": str(project_id),
                    "userId": str(user_id),
                    "assigned_agent": None,
                }
            ]
        ),
    )
    project_members.insert_one(
        {"project_id": project_id, "user_id": user_id, "role": "reporter"}
    )

    response = ticket.get_ticket_capabilities(
        str(ticket_id), current_user={"_id": user_id, "role": "customer"}
    )

    assert response["capabilities"]
    assert response["context"] == {
        "is_creator": True,
        "is_assignee": False,
        "can_upload_attachments": True,
    }
    assert response["can"][Capability.UPLOAD_ATTACHMENTS.value] is True


def test_super_admin_can_change_global_role(monkeypatch, fake_collection):
    user_id = ObjectId()
    users = fake_collection.__class__([{"_id": user_id}])
    monkeypatch.setattr(registration, "user_collection", users)

    response = registration.change_user_role(
        str(user_id),
        ChangeGlobalRole(role="admin"),
        current_user={"_id": ObjectId(), "role": "super_admin"},
    )

    assert response["role"] == "admin"
    assert users.documents[0]["role"] == "admin"

    response = registration.change_user_role(
        str(user_id),
        ChangeGlobalRole(role=None),
        current_user={"_id": ObjectId(), "role": "super_admin"},
    )

    assert response["role"] is None
    assert "role" not in users.documents[0]
