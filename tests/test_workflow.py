import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.api import project, ticket
from app.main import app
from app.models.ticket import BulkAssignRequest, BulkTransitionRequest
from app.models.transition import ProjectWorkflowUpdate, TransitionCreate


def _transition(
    project_id,
    name,
    sources,
    target,
    *,
    roles=None,
    requires_assignee=False,
    transition_id=None,
):
    return {
        "_id": transition_id or ObjectId(),
        "project_id": project_id,
        "name": name,
        "from_status_ids": [str(status_id) for status_id in sources],
        "to_status_id": str(target),
        "guard": {
            "allowed_roles": roles or ["lead", "member"],
            "requires_assignee": requires_assignee,
        },
    }


def test_project_lead_configures_collection_backed_transition_with_many_sources(
    monkeypatch,
    fake_collection,
    transitions,
    grant_project_role,
):
    project_id = ObjectId()
    lead_id = ObjectId()
    open_id = ObjectId()
    reopened_id = ObjectId()
    done_id = ObjectId()
    projects = fake_collection.__class__(
        [
            {
                "_id": project_id,
                "assigned_statuses": [str(open_id), str(reopened_id), str(done_id)],
                "workflow_transitions": [{"legacy": True}],
            }
        ]
    )
    statuses = fake_collection.__class__(
        [
            {"_id": open_id, "name": "Open"},
            {"_id": reopened_id, "name": "Reopened"},
            {"_id": done_id, "name": "Done"},
        ]
    )
    monkeypatch.setattr(project, "projects_collection", projects)
    monkeypatch.setattr(project, "statuses_collection", statuses)
    monkeypatch.setattr(project, "transitions_collection", transitions)
    grant_project_role(project_id, lead_id, "lead")

    response = project.configure_project_workflow(
        str(project_id),
        ProjectWorkflowUpdate(
            transitions=[
                TransitionCreate(
                    name="Complete",
                    from_status_ids=[str(open_id), str(reopened_id)],
                    to_status_id=str(done_id),
                    guard={
                        "allowed_roles": ["lead", "member"],
                        "requires_assignee": True,
                    },
                )
            ]
        ),
        current_user={"_id": lead_id, "role": "user"},
    )

    stored = transitions.documents[0]
    assert stored["project_id"] == project_id
    assert stored["from_status_ids"] == [str(open_id), str(reopened_id)]
    assert stored["to_status_id"] == str(done_id)
    assert stored["guard"] == {
        "allowed_roles": ["lead", "member"],
        "requires_assignee": True,
    }
    assert response["transitions"][0]["_id"] == str(stored["_id"])
    assert "workflow_transitions" not in projects.documents[0]


def test_workflow_configuration_allows_duplicate_names_with_distinct_ids(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    lead_id = ObjectId()
    first_id = ObjectId()
    second_id = ObjectId()
    monkeypatch.setattr(
        project,
        "projects_collection",
        fake_collection.__class__(
            [
                {
                    "_id": project_id,
                    "assigned_statuses": [str(first_id), str(second_id)],
                }
            ]
        ),
    )
    monkeypatch.setattr(
        project,
        "statuses_collection",
        fake_collection.__class__(
            [
                {"_id": first_id, "name": "First"},
                {"_id": second_id, "name": "Second"},
            ]
        ),
    )
    grant_project_role(project_id, lead_id, "lead")
    payload = ProjectWorkflowUpdate(
        transitions=[
            TransitionCreate(
                name="Move",
                from_status_ids=[str(first_id)],
                to_status_id=str(second_id),
            ),
            TransitionCreate(
                name="move",
                from_status_ids=[str(second_id)],
                to_status_id=str(first_id),
            ),
        ]
    )

    response = project.configure_project_workflow(
        str(project_id),
        payload,
        current_user={"_id": lead_id, "role": "user"},
    )

    assert len(response["transitions"]) == 2
    assert response["transitions"][0]["_id"] != response["transitions"][1]["_id"]


def test_transition_id_accepts_any_configured_source_and_logs_activity(
    monkeypatch,
    fake_collection,
    transitions,
    ticket_activities,
    grant_project_role,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    open_id = ObjectId()
    reopened_id = ObjectId()
    done_id = ObjectId()
    member_id = ObjectId()
    transition = _transition(
        project_id,
        "Complete",
        [open_id, reopened_id],
        done_id,
        roles=["member"],
    )
    transitions.insert_one(transition)
    projects = fake_collection.__class__(
        [
            {
                "_id": project_id,
                "assigned_statuses": [str(open_id), str(reopened_id), str(done_id)],
            }
        ]
    )
    statuses = fake_collection.__class__([{"_id": done_id, "name": "Done"}])
    tickets = fake_collection.__class__(
        [
            {
                "_id": ticket_id,
                "project_id": str(project_id),
                "ticket_code": "FLOW-1",
                "current_status_id": str(reopened_id),
                "current_status_name": "Reopened",
            }
        ]
    )
    monkeypatch.setattr(ticket, "projects_collection", projects)
    monkeypatch.setattr(ticket, "statuses_collection", statuses)
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "transitions_collection", transitions)
    monkeypatch.setattr(ticket, "notifications_collection", fake_collection.__class__())
    grant_project_role(project_id, member_id, "member")

    response = ticket.transition_ticket(
        str(ticket_id),
        str(transition["_id"]),
        current_user={"_id": member_id, "role": "agent"},
    )

    assert response["transition_id"] == str(transition["_id"])
    assert response["current_status"] == "Done"
    assert tickets.documents[0]["current_status_id"] == str(done_id)
    assert ticket_activities.documents[-1]["action"] == "ticket_transitioned"
    assert ticket_activities.documents[-1]["changes"] == [
        {
            "field": "current_status_id",
            "old_value": str(reopened_id),
            "new_value": str(done_id),
        },
        {
            "field": "current_status_name",
            "old_value": "Reopened",
            "new_value": "Done",
        },
    ]


def test_transition_rejects_current_status_outside_allowed_sources(
    monkeypatch,
    fake_collection,
    transitions,
    grant_project_role,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    open_id = ObjectId()
    progress_id = ObjectId()
    done_id = ObjectId()
    member_id = ObjectId()
    transition = _transition(project_id, "Complete", [progress_id], done_id)
    transitions.insert_one(transition)
    monkeypatch.setattr(
        ticket,
        "projects_collection",
        fake_collection.__class__(
            [
                {
                    "_id": project_id,
                    "assigned_statuses": [str(open_id), str(progress_id), str(done_id)],
                }
            ]
        ),
    )
    monkeypatch.setattr(
        ticket,
        "ticket_collection",
        fake_collection.__class__(
            [
                {
                    "_id": ticket_id,
                    "project_id": str(project_id),
                    "current_status_id": str(open_id),
                }
            ]
        ),
    )
    grant_project_role(project_id, member_id, "member")

    with pytest.raises(HTTPException) as exc_info:
        ticket.transition_ticket(
            str(ticket_id),
            str(transition["_id"]),
            current_user={"_id": member_id, "role": "agent"},
        )

    assert exc_info.value.status_code == 400
    assert "allowed transition source" in exc_info.value.detail


def test_transition_rejects_transition_from_another_project(
    monkeypatch,
    fake_collection,
    transitions,
    grant_project_role,
):
    project_id = ObjectId()
    other_project_id = ObjectId()
    ticket_id = ObjectId()
    open_id = ObjectId()
    done_id = ObjectId()
    member_id = ObjectId()
    transition = _transition(other_project_id, "Complete", [open_id], done_id)
    transitions.insert_one(transition)
    monkeypatch.setattr(
        ticket,
        "projects_collection",
        fake_collection.__class__(
            [
                {
                    "_id": project_id,
                    "assigned_statuses": [str(open_id), str(done_id)],
                }
            ]
        ),
    )
    monkeypatch.setattr(
        ticket,
        "ticket_collection",
        fake_collection.__class__(
            [
                {
                    "_id": ticket_id,
                    "project_id": str(project_id),
                    "current_status_id": str(open_id),
                }
            ]
        ),
    )
    grant_project_role(project_id, member_id, "member")

    with pytest.raises(HTTPException) as exc_info:
        ticket.transition_ticket(
            str(ticket_id),
            str(transition["_id"]),
            current_user={"_id": member_id, "role": "agent"},
        )

    assert exc_info.value.status_code == 400
    assert "different project" in exc_info.value.detail


def test_transition_enforces_role_and_assignee_guards(
    monkeypatch,
    fake_collection,
    transitions,
    project_members,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    open_id = ObjectId()
    progress_id = ObjectId()
    user_id = ObjectId()
    transition = _transition(
        project_id,
        "Start",
        [open_id],
        progress_id,
        roles=["lead"],
        requires_assignee=True,
    )
    transitions.insert_one(transition)
    monkeypatch.setattr(
        ticket,
        "projects_collection",
        fake_collection.__class__(
            [
                {
                    "_id": project_id,
                    "assigned_statuses": [str(open_id), str(progress_id)],
                }
            ]
        ),
    )
    monkeypatch.setattr(
        ticket,
        "ticket_collection",
        fake_collection.__class__(
            [
                {
                    "_id": ticket_id,
                    "project_id": str(project_id),
                    "current_status_id": str(open_id),
                    "assigned_agent": None,
                }
            ]
        ),
    )
    membership = {
        "_id": ObjectId(),
        "project_id": project_id,
        "user_id": user_id,
        "role": "member",
    }
    project_members.documents.append(membership)

    with pytest.raises(HTTPException) as exc_info:
        ticket.transition_ticket(
            str(ticket_id),
            str(transition["_id"]),
            current_user={"_id": user_id, "role": "agent"},
        )
    assert exc_info.value.status_code == 403

    project_members.documents[0]["role"] = "lead"
    with pytest.raises(HTTPException) as exc_info:
        ticket.transition_ticket(
            str(ticket_id),
            str(transition["_id"]),
            current_user={"_id": user_id, "role": "agent"},
        )
    assert exc_info.value.status_code == 400
    assert "assigned" in exc_info.value.detail.lower()


def test_bulk_transition_uses_one_transition_id_and_reports_failures(
    monkeypatch,
    fake_collection,
    transitions,
    grant_project_role,
):
    project_id = ObjectId()
    first_ticket_id = ObjectId()
    second_ticket_id = ObjectId()
    open_id = ObjectId()
    done_id = ObjectId()
    member_id = ObjectId()
    transition = _transition(project_id, "Complete", [open_id], done_id)
    transitions.insert_one(transition)
    monkeypatch.setattr(
        ticket,
        "projects_collection",
        fake_collection.__class__(
            [
                {
                    "_id": project_id,
                    "assigned_statuses": [str(open_id), str(done_id)],
                }
            ]
        ),
    )
    monkeypatch.setattr(
        ticket,
        "statuses_collection",
        fake_collection.__class__([{"_id": done_id, "name": "Done"}]),
    )
    monkeypatch.setattr(
        ticket,
        "ticket_collection",
        fake_collection.__class__(
            [
                {
                    "_id": first_ticket_id,
                    "project_id": str(project_id),
                    "current_status_id": str(open_id),
                    "current_status_name": "Open",
                },
                {
                    "_id": second_ticket_id,
                    "project_id": str(project_id),
                    "current_status_id": str(done_id),
                    "current_status_name": "Done",
                },
            ]
        ),
    )
    monkeypatch.setattr(ticket, "notifications_collection", fake_collection.__class__())
    grant_project_role(project_id, member_id, "member")

    response = ticket.bulk_transition_tickets(
        BulkTransitionRequest(
            ticket_ids=[str(first_ticket_id), str(second_ticket_id)],
            transition_id=str(transition["_id"]),
        ),
        current_user={"_id": member_id, "role": "agent"},
    )

    assert [item["ticket_id"] for item in response["succeeded"]] == [
        str(first_ticket_id)
    ]
    assert response["failed"][0]["ticket_id"] == str(second_ticket_id)
    assert response["failed"][0]["status_code"] == 400


def test_assignment_and_bulk_assignment_write_activity(
    monkeypatch,
    fake_collection,
    ticket_activities,
    grant_project_role,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    missing_ticket_id = ObjectId()
    lead_id = ObjectId()
    assignee_id = ObjectId()
    tickets = fake_collection.__class__(
        [
            {
                "_id": ticket_id,
                "project_id": str(project_id),
                "assigned_agent": None,
            }
        ]
    )
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(
        ticket,
        "user_collection",
        fake_collection.__class__([{"_id": assignee_id}]),
    )
    monkeypatch.setattr(ticket, "notifications_collection", fake_collection.__class__())
    grant_project_role(project_id, lead_id, "lead")
    grant_project_role(project_id, assignee_id, "member")

    response = ticket.bulk_assign_tickets(
        BulkAssignRequest(
            ticket_ids=[str(ticket_id), str(missing_ticket_id)],
            agent_id=str(assignee_id),
        ),
        current_user={"_id": lead_id, "role": "agent"},
    )

    assert response["succeeded"] == [{"ticket_id": str(ticket_id)}]
    assert ticket_activities.documents[-1]["action"] == "ticket_assigned"
    assert ticket_activities.documents[-1]["changes"][0] == {
        "field": "assigned_agent",
        "old_value": None,
        "new_value": str(assignee_id),
    }


def test_only_transition_id_endpoint_can_change_workflow_status_in_openapi():
    paths = app.openapi()["paths"]

    assert "post" in paths["/tickets/{ticket_id}/transitions/{transition_id}"]
    assert set(paths["/tickets/{ticket_id}/transitions"]) == {"get"}
    assert "/{ticket_id}/move-forward" not in paths
    assert "/tickets/{ticket_id}/status" not in paths
    assert "/tickets/{ticket_id}/reopen" not in paths
    assert "/tickets/bulk/transition" in paths
