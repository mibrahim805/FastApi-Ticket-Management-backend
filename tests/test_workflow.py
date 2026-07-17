import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.api import project, ticket
from app.main import app
from app.models.project import ProjectWorkflowUpdate, WorkflowTransition
from app.models.ticket import (
    BulkAssignRequest,
    BulkTransitionRequest,
    TicketTransitionRequest,
)


def _transition(name, source, target, roles=None, requires_assignee=False):
    return {
        "name": name,
        "from_status_id": str(source),
        "to_status_id": str(target),
        "guards": {
            "allowed_roles": roles or ["lead", "member"],
            "requires_assignee": requires_assignee,
        },
    }


def test_project_lead_can_configure_branching_and_backward_workflow(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    lead_id = ObjectId()
    open_id = ObjectId()
    progress_id = ObjectId()
    done_id = ObjectId()
    projects = fake_collection.__class__(
        [
            {
                "_id": project_id,
                "assigned_statuses": [str(open_id), str(progress_id), str(done_id)],
            }
        ]
    )
    statuses = fake_collection.__class__(
        [
            {"_id": open_id, "name": "Open"},
            {"_id": progress_id, "name": "In Progress"},
            {"_id": done_id, "name": "Done"},
        ]
    )
    monkeypatch.setattr(project, "projects_collection", projects)
    monkeypatch.setattr(project, "statuses_collection", statuses)
    grant_project_role(project_id, lead_id, "lead")
    payload = ProjectWorkflowUpdate(
        transitions=[
            WorkflowTransition(**_transition("Start", open_id, progress_id)),
            WorkflowTransition(**_transition("Skip", open_id, done_id)),
            WorkflowTransition(**_transition("Return", progress_id, open_id)),
            WorkflowTransition(**_transition("Reopen", done_id, open_id)),
        ]
    )

    response = project.configure_project_workflow(
        str(project_id),
        payload,
        current_user={"_id": lead_id, "role": "user"},
    )

    assert [item["name"] for item in response["transitions"]] == [
        "Start",
        "Skip",
        "Return",
        "Reopen",
    ]
    assert projects.documents[0]["workflow_transitions"] == response["transitions"]


def test_transition_names_only_need_to_be_unique_per_source_status(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    lead_id = ObjectId()
    first_id = ObjectId()
    second_id = ObjectId()
    projects = fake_collection.__class__(
        [
            {
                "_id": project_id,
                "assigned_statuses": [str(first_id), str(second_id)],
            }
        ]
    )
    statuses = fake_collection.__class__(
        [
            {"_id": first_id, "name": "First"},
            {"_id": second_id, "name": "Second"},
        ]
    )
    monkeypatch.setattr(project, "projects_collection", projects)
    monkeypatch.setattr(project, "statuses_collection", statuses)
    grant_project_role(project_id, lead_id, "lead")
    payload = ProjectWorkflowUpdate(
        transitions=[
            WorkflowTransition(**_transition("Move", first_id, second_id)),
            WorkflowTransition(**_transition("Move", second_id, first_id)),
        ]
    )

    response = project.configure_project_workflow(
        str(project_id),
        payload,
        current_user={"_id": lead_id, "role": "user"},
    )

    assert len(response["transitions"]) == 2


def test_named_transition_enforces_role_and_assignee_guards(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    open_id = ObjectId()
    progress_id = ObjectId()
    member_id = ObjectId()
    projects = fake_collection.__class__(
        [
            {
                "_id": project_id,
                "assigned_statuses": [str(open_id), str(progress_id)],
                "workflow_transitions": [
                    _transition(
                        "Start",
                        open_id,
                        progress_id,
                        roles=["lead"],
                        requires_assignee=True,
                    )
                ],
            }
        ]
    )
    tickets = fake_collection.__class__(
        [
            {
                "_id": ticket_id,
                "project_id": str(project_id),
                "current_status_id": str(open_id),
                "current_status_name": "Open",
                "assigned_agent": None,
            }
        ]
    )
    monkeypatch.setattr(ticket, "projects_collection", projects)
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    grant_project_role(project_id, member_id, "member")

    with pytest.raises(HTTPException) as exc_info:
        ticket.transition_ticket(
            str(ticket_id),
            TicketTransitionRequest(transition_name="Start"),
            current_user={"_id": member_id, "role": "agent"},
        )

    assert exc_info.value.status_code == 403

    project_membership = ticket.project_members_collection.documents[0]
    project_membership["role"] = "lead"
    with pytest.raises(HTTPException) as exc_info:
        ticket.transition_ticket(
            str(ticket_id),
            TicketTransitionRequest(transition_name="Start"),
            current_user={"_id": member_id, "role": "agent"},
        )

    assert exc_info.value.status_code == 400
    assert "assigned" in exc_info.value.detail.lower()


def test_named_transition_can_move_backward_and_clears_resolution_dates(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    open_id = ObjectId()
    done_id = ObjectId()
    lead_id = ObjectId()
    projects = fake_collection.__class__(
        [
            {
                "_id": project_id,
                "assigned_statuses": [str(open_id), str(done_id)],
                "workflow_transitions": [
                    _transition("Reopen", done_id, open_id, roles=["lead"])
                ],
            }
        ]
    )
    statuses = fake_collection.__class__([{"_id": open_id, "name": "To Do"}])
    tickets = fake_collection.__class__(
        [
            {
                "_id": ticket_id,
                "project_id": str(project_id),
                "ticket_code": "FLOW-1",
                "current_status_id": str(done_id),
                "current_status_name": "Done",
                "resolved_date": "old",
                "closed_date": "old",
            }
        ]
    )
    monkeypatch.setattr(ticket, "projects_collection", projects)
    monkeypatch.setattr(ticket, "statuses_collection", statuses)
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "notifications_collection", fake_collection.__class__())
    grant_project_role(project_id, lead_id, "lead")

    response = ticket.reopen_ticket(
        str(ticket_id),
        current_user={"_id": lead_id, "role": "agent"},
    )

    assert response["transition"] == "Reopen"
    assert response["current_status"] == "To Do"
    assert tickets.documents[0]["resolved_date"] is None
    assert tickets.documents[0]["closed_date"] is None


def test_legacy_reopen_uses_project_default_status(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    todo_id = ObjectId()
    closed_id = ObjectId()
    lead_id = ObjectId()
    projects = fake_collection.__class__(
        [
            {
                "_id": project_id,
                "assigned_statuses": [str(todo_id), str(closed_id)],
                "default_status_id": str(todo_id),
                "workflow_transitions": [],
            }
        ]
    )
    statuses = fake_collection.__class__([{"_id": todo_id, "name": "To Do"}])
    tickets = fake_collection.__class__(
        [
            {
                "_id": ticket_id,
                "project_id": str(project_id),
                "ticket_code": "OLD-1",
                "current_status_id": str(closed_id),
                "current_status_name": "closed",
                "resolved_date": "old",
                "closed_date": "old",
            }
        ]
    )
    monkeypatch.setattr(ticket, "projects_collection", projects)
    monkeypatch.setattr(ticket, "statuses_collection", statuses)
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "notifications_collection", fake_collection.__class__())
    grant_project_role(project_id, lead_id, "lead")

    response = ticket.reopen_ticket(
        str(ticket_id),
        current_user={"_id": lead_id, "role": "agent"},
    )

    assert response["current_status"] == "To Do"
    assert tickets.documents[0]["current_status_id"] == str(todo_id)
    assert tickets.documents[0]["resolved_date"] is None
    assert tickets.documents[0]["closed_date"] is None


def test_move_forward_requires_named_transition_when_workflow_branches(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    open_id = ObjectId()
    progress_id = ObjectId()
    done_id = ObjectId()
    member_id = ObjectId()
    projects = fake_collection.__class__(
        [
            {
                "_id": project_id,
                "assigned_statuses": [str(open_id), str(progress_id), str(done_id)],
                "workflow_transitions": [
                    _transition("Start", open_id, progress_id),
                    _transition("Skip", open_id, done_id),
                ],
            }
        ]
    )
    tickets = fake_collection.__class__(
        [
            {
                "_id": ticket_id,
                "project_id": str(project_id),
                "current_status_id": str(open_id),
                "current_status_name": "Open",
            }
        ]
    )
    monkeypatch.setattr(ticket, "projects_collection", projects)
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    grant_project_role(project_id, member_id, "member")

    with pytest.raises(HTTPException) as exc_info:
        ticket.move_ticket_forward(
            str(ticket_id),
            current_user={"_id": member_id, "role": "agent"},
        )

    assert exc_info.value.status_code == 409


def test_bulk_transition_reports_successes_and_failures(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    first_ticket_id = ObjectId()
    second_ticket_id = ObjectId()
    open_id = ObjectId()
    done_id = ObjectId()
    member_id = ObjectId()
    projects = fake_collection.__class__(
        [
            {
                "_id": project_id,
                "assigned_statuses": [str(open_id), str(done_id)],
                "workflow_transitions": [_transition("Complete", open_id, done_id)],
            }
        ]
    )
    statuses = fake_collection.__class__([{"_id": done_id, "name": "Done"}])
    tickets = fake_collection.__class__(
        [
            {
                "_id": first_ticket_id,
                "project_id": str(project_id),
                "ticket_code": "BULK-1",
                "current_status_id": str(open_id),
                "current_status_name": "Open",
            },
            {
                "_id": second_ticket_id,
                "project_id": str(project_id),
                "ticket_code": "BULK-2",
                "current_status_id": str(done_id),
                "current_status_name": "Done",
            },
        ]
    )
    monkeypatch.setattr(ticket, "projects_collection", projects)
    monkeypatch.setattr(ticket, "statuses_collection", statuses)
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "notifications_collection", fake_collection.__class__())
    grant_project_role(project_id, member_id, "member")

    response = ticket.bulk_transition_tickets(
        BulkTransitionRequest(
            ticket_ids=[str(first_ticket_id), str(second_ticket_id)],
            transition_name="Complete",
        ),
        current_user={"_id": member_id, "role": "agent"},
    )

    assert response["total"] == 2
    assert [item["ticket_id"] for item in response["succeeded"]] == [
        str(first_ticket_id)
    ]
    assert response["failed"] == [
        {
            "ticket_id": str(second_ticket_id),
            "status_code": 400,
            "detail": "Transition is not available",
        }
    ]


def test_bulk_assign_reports_successes_and_failures(
    monkeypatch,
    fake_collection,
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
    users = fake_collection.__class__([{"_id": assignee_id}])
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "user_collection", users)
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
    assert response["failed"] == [
        {
            "ticket_id": str(missing_ticket_id),
            "status_code": 404,
            "detail": "Ticket not found",
        }
    ]
    assert tickets.documents[0]["assigned_agent"] == str(assignee_id)


def test_workflow_and_bulk_routes_are_exposed_in_openapi():
    paths = app.openapi()["paths"]

    assert "/projects/{project_id}/workflow" in paths
    assert "/tickets/{ticket_id}/transitions" in paths
    assert "/tickets/bulk/transition" in paths
    assert "/tickets/bulk/assign" in paths
