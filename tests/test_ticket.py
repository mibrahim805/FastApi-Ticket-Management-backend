from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.api import ticket
from app.api.ticket import calculate_due_date
from app.main import app
from app.models.ticket import (
    AssignAgent,
    Category,
    IssueType,
    Priority,
    TicketCreate,
)


@pytest.mark.parametrize(
    "priority, days",
    [
        ("high", 1),
        ("medium", 3),
        ("low", 7),
    ],
)
def test_calculate_due_date(monkeypatch, priority, days):
    fixed_time = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)

    class FakeDateTime:
        @classmethod
        def now(cls, tz=None):
            return fixed_time

    monkeypatch.setattr("app.api.ticket.datetime", FakeDateTime)

    due_date = calculate_due_date(priority)

    assert due_date == fixed_time + timedelta(days=days)


def test_calculate_due_date_invalid_priority():
    with pytest.raises(ValueError):
        calculate_due_date("invalid")


@pytest.mark.parametrize(
    "issue_type, expected_issue_type",
    [(None, "task"), (IssueType.BUG, "bug")],
)
def test_create_ticket_assigns_first_status_and_updates_counter(
    monkeypatch,
    fake_collection,
    grant_project_role,
    issue_type,
    expected_issue_type,
):
    project_id = ObjectId()
    status_id = ObjectId()
    current_user = {"_id": ObjectId(), "role": "agent"}

    projects = fake_collection.__class__(
        [
            {
                "_id": project_id,
                "code": "SUP",
                "assigned_agent": str(current_user["_id"]),
                "assigned_statuses": [str(status_id)],
                "ticket_counter": 2,
            }
        ]
    )
    statuses = fake_collection.__class__([{"_id": status_id, "name": "Open"}])
    tickets = fake_collection.__class__()
    notifications = fake_collection.__class__()
    counters = fake_collection.__class__()

    monkeypatch.setattr(ticket, "projects_collection", projects)
    monkeypatch.setattr(ticket, "statuses_collection", statuses)
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "notifications_collection", notifications)
    monkeypatch.setattr(ticket, "counters_collection", counters)
    monkeypatch.setattr(ticket, "log_activity", lambda **kwargs: None)
    monkeypatch.setattr(ticket, "calculate_due_date", lambda priority: "due-date")
    grant_project_role(project_id, current_user["_id"], "member")

    payload_data = {
        "project_id": str(project_id),
        "title": "Printer issue",
        "description": "Printer is offline",
        "category": Category.SUPPORT,
        "priority": Priority.HIGH,
    }
    if issue_type is not None:
        payload_data["issue_type"] = issue_type
    payload = TicketCreate(**payload_data)

    response = ticket.create_ticket(payload, current_user=current_user)

    assert response["ticket_code"].startswith("SUP-")
    assert response["current_status_id"] == str(status_id)
    assert response["issue_type"] == expected_issue_type
    assert response["labels"] == []
    assert response["due_date"] == "due-date"
    assert tickets.documents[0]["issue_type"] == expected_issue_type
    assert tickets.documents[0]["labels"] == []
    assert len(tickets.documents) == 1
    assert len(notifications.documents) == 1


def test_create_ticket_persists_epic_reference(
    monkeypatch, fake_collection, grant_project_role
):
    project_id = ObjectId()
    status_id = ObjectId()
    epic_id = ObjectId()
    current_user_id = ObjectId()
    projects = fake_collection.__class__(
        [
            {
                "_id": project_id,
                "code": "SUP",
                "assigned_statuses": [str(status_id)],
            }
        ]
    )
    statuses = fake_collection.__class__([{"_id": status_id, "name": "Open"}])
    tickets = fake_collection.__class__(
        [
            {
                "_id": epic_id,
                "project_id": str(project_id),
                "ticket_code": "SUP-1",
                "issue_type": "epic",
            }
        ]
    )
    notifications = fake_collection.__class__()
    monkeypatch.setattr(ticket, "projects_collection", projects)
    monkeypatch.setattr(ticket, "statuses_collection", statuses)
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "notifications_collection", notifications)
    monkeypatch.setattr(ticket, "counters_collection", fake_collection.__class__())
    monkeypatch.setattr(ticket, "log_activity", lambda **kwargs: None)
    monkeypatch.setattr(ticket, "calculate_due_date", lambda priority: "due-date")
    grant_project_role(project_id, current_user_id, "member")

    response = ticket.create_ticket(
        TicketCreate(
            project_id=str(project_id),
            title="Printer improvements",
            description="Improve printer reliability",
            category=Category.FEATURE_REQUEST,
            issue_type=IssueType.STORY,
            epic_id=str(epic_id),
            labels=["backend", "customer-request"],
            priority=Priority.MEDIUM,
        ),
        current_user={"_id": current_user_id, "role": "agent"},
    )

    assert response["epic_id"] == str(epic_id)
    assert response["labels"] == ["backend", "customer-request"]
    assert tickets.documents[-1]["epic_id"] == str(epic_id)
    assert tickets.documents[-1]["labels"] == ["backend", "customer-request"]


def test_create_ticket_rejects_non_epic_reference(
    monkeypatch, fake_collection, grant_project_role
):
    project_id = ObjectId()
    referenced_issue_id = ObjectId()
    current_user_id = ObjectId()
    monkeypatch.setattr(
        ticket,
        "projects_collection",
        fake_collection.__class__([{"_id": project_id, "code": "SUP"}]),
    )
    monkeypatch.setattr(
        ticket,
        "ticket_collection",
        fake_collection.__class__(
            [
                {
                    "_id": referenced_issue_id,
                    "project_id": str(project_id),
                    "issue_type": "task",
                }
            ]
        ),
    )
    grant_project_role(project_id, current_user_id, "member")

    with pytest.raises(HTTPException) as exc_info:
        ticket.create_ticket(
            TicketCreate(
                project_id=str(project_id),
                title="Child issue",
                description="Invalid epic reference",
                category=Category.SUPPORT,
                issue_type=IssueType.SUBTASK,
                epic_id=str(referenced_issue_id),
                priority=Priority.LOW,
            ),
            current_user={"_id": current_user_id, "role": "agent"},
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Referenced issue is not an epic"


def test_get_epic_returns_child_issues_and_progress(
    monkeypatch, fake_collection, grant_project_role
):
    project_id = ObjectId()
    epic_id = ObjectId()
    other_epic_id = ObjectId()
    todo_status_id = ObjectId()
    done_status_id = ObjectId()
    current_user_id = ObjectId()
    projects = fake_collection.__class__(
        [
            {
                "_id": project_id,
                "code": "SUP",
                "assigned_statuses": [str(todo_status_id), str(done_status_id)],
            }
        ]
    )
    tickets = fake_collection.__class__(
        [
            {
                "_id": epic_id,
                "project_id": str(project_id),
                "ticket_code": "SUP-1",
                "issue_type": "epic",
            },
            {
                "_id": ObjectId(),
                "project_id": str(project_id),
                "ticket_code": "SUP-2",
                "issue_type": "story",
                "epic_id": str(epic_id),
                "current_status_id": str(done_status_id),
                "current_status_name": "Deployed",
            },
            {
                "_id": ObjectId(),
                "project_id": str(project_id),
                "ticket_code": "SUP-3",
                "issue_type": "bug",
                "epic_id": str(epic_id),
                "current_status_id": str(todo_status_id),
                "current_status_name": "Open",
                "resolved_date": datetime(2026, 7, 1, tzinfo=timezone.utc),
            },
            {
                "_id": ObjectId(),
                "project_id": str(project_id),
                "ticket_code": "SUP-4",
                "issue_type": "task",
                "epic_id": str(epic_id),
                "current_status_id": str(todo_status_id),
                "current_status_name": "Open",
            },
            {
                "_id": ObjectId(),
                "project_id": str(project_id),
                "ticket_code": "SUP-5",
                "issue_type": "story",
                "epic_id": str(other_epic_id),
                "current_status_id": str(done_status_id),
            },
        ]
    )
    monkeypatch.setattr(ticket, "projects_collection", projects)
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    grant_project_role(project_id, current_user_id, "reporter")

    response = ticket.get_epic(
        str(epic_id),
        current_user={"_id": current_user_id, "role": "agent"},
    )

    assert response["epic"]["ticket_code"] == "SUP-1"
    assert {issue["ticket_code"] for issue in response["issues"]} == {
        "SUP-2",
        "SUP-3",
        "SUP-4",
    }
    assert response["progress"] == {"done": 2, "total": 3}


def test_create_ticket_rejects_missing_project(monkeypatch, fake_collection):
    monkeypatch.setattr(ticket, "projects_collection", fake_collection)
    monkeypatch.setattr(ticket, "statuses_collection", fake_collection.__class__())

    payload = TicketCreate(
        project_id=str(ObjectId()),
        title="Printer issue",
        description="Printer is offline",
        category=Category.SUPPORT,
        priority=Priority.HIGH,
    )

    with pytest.raises(HTTPException) as exc_info:
        ticket.create_ticket(payload, current_user={"_id": ObjectId(), "role": "agent"})

    assert exc_info.value.status_code == 404


def test_create_ticket_rejects_project_with_no_statuses(
    monkeypatch, fake_collection, grant_project_role
):
    project_id = ObjectId()
    projects = fake_collection.__class__(
        [
            {
                "_id": project_id,
                "code": "SUP",
                "assigned_agent": str(ObjectId()),
                "assigned_statuses": [],
            }
        ]
    )
    monkeypatch.setattr(ticket, "projects_collection", projects)
    monkeypatch.setattr(ticket, "statuses_collection", fake_collection.__class__())
    user_id = ObjectId()
    grant_project_role(project_id, user_id, "reporter")

    payload = TicketCreate(
        project_id=str(project_id),
        title="Printer issue",
        description="Printer is offline",
        category=Category.SUPPORT,
        priority=Priority.HIGH,
    )

    with pytest.raises(HTTPException) as exc_info:
        ticket.create_ticket(
            payload, current_user={"_id": user_id, "role": "super_admin"}
        )

    assert exc_info.value.status_code == 400


def test_create_ticket_rejects_missing_first_status(
    monkeypatch, fake_collection, grant_project_role
):
    project_id = ObjectId()
    missing_status_id = ObjectId()
    projects = fake_collection.__class__(
        [
            {
                "_id": project_id,
                "code": "SUP",
                "assigned_agent": str(ObjectId()),
                "assigned_statuses": [str(missing_status_id)],
            }
        ]
    )
    monkeypatch.setattr(ticket, "projects_collection", projects)
    monkeypatch.setattr(ticket, "statuses_collection", fake_collection.__class__())
    user_id = ObjectId()
    grant_project_role(project_id, user_id, "reporter")

    payload = TicketCreate(
        project_id=str(project_id),
        title="Printer issue",
        description="Printer is offline",
        category=Category.SUPPORT,
        priority=Priority.HIGH,
    )

    with pytest.raises(HTTPException) as exc_info:
        ticket.create_ticket(
            payload, current_user={"_id": user_id, "role": "super_admin"}
        )

    assert exc_info.value.status_code == 400


def test_create_ticket_rejects_invalid_project_id(monkeypatch, fake_collection):
    monkeypatch.setattr(ticket, "projects_collection", fake_collection)
    monkeypatch.setattr(ticket, "statuses_collection", fake_collection.__class__())

    payload = TicketCreate(
        project_id="invalid-project-id",
        title="Printer issue",
        description="Printer is offline",
        category=Category.SUPPORT,
        priority=Priority.HIGH,
    )

    with pytest.raises(HTTPException) as exc_info:
        ticket.create_ticket(payload, current_user={"_id": ObjectId(), "role": "agent"})

    assert exc_info.value.status_code == 400


def test_create_ticket_allows_any_project_member(
    monkeypatch, fake_collection, grant_project_role
):
    project_id = ObjectId()
    status_id = ObjectId()
    projects = fake_collection.__class__(
        [
            {
                "_id": project_id,
                "code": "SUP",
                "assigned_agent": str(ObjectId()),
                "assigned_statuses": [str(status_id)],
            }
        ]
    )
    statuses = fake_collection.__class__([{"_id": status_id, "name": "Open"}])
    tickets = fake_collection.__class__()
    notifications = fake_collection.__class__()
    counters = fake_collection.__class__()

    monkeypatch.setattr(ticket, "projects_collection", projects)
    monkeypatch.setattr(ticket, "statuses_collection", statuses)
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "notifications_collection", notifications)
    monkeypatch.setattr(ticket, "counters_collection", counters)
    monkeypatch.setattr(ticket, "log_activity", lambda **kwargs: None)
    monkeypatch.setattr(ticket, "calculate_due_date", lambda priority: "due-date")
    reporter_id = ObjectId()
    grant_project_role(project_id, reporter_id, "reporter")

    payload = TicketCreate(
        project_id=str(project_id),
        title="Printer issue",
        description="Printer is offline",
        category=Category.SUPPORT,
        priority=Priority.HIGH,
    )

    response = ticket.create_ticket(
        payload, current_user={"_id": reporter_id, "role": "agent"}
    )

    assert response["ticket_code"].startswith("SUP-")
    assert len(tickets.documents) == 1


def test_get_tickets_includes_tickets_assigned_to_current_user(
    monkeypatch, fake_collection, grant_project_role
):
    project_key = "SUPPORT"
    current_user_id = ObjectId()
    other_user_id = ObjectId()

    project_id = ObjectId()
    projects = fake_collection.__class__([{"_id": project_id, "code": project_key}])
    tickets = fake_collection.__class__(
        [
            {
                "_id": ObjectId(),
                "project_id": str(project_id),
                "project_key": project_key,
                "ticket_code": "TKT-1",
                "userId": str(current_user_id),
                "assigned_agent": None,
                "title": "Created by me",
                "created_date": datetime(2026, 7, 1, tzinfo=timezone.utc),
            },
            {
                "_id": ObjectId(),
                "project_id": str(project_id),
                "project_key": project_key,
                "ticket_code": "TKT-2",
                "userId": str(other_user_id),
                "assigned_agent": str(current_user_id),
                "title": "Assigned to me",
                "created_date": datetime(2026, 7, 2, tzinfo=timezone.utc),
            },
            {
                "_id": ObjectId(),
                "project_id": str(project_id),
                "project_key": project_key,
                "ticket_code": "TKT-3",
                "userId": str(other_user_id),
                "assigned_agent": str(other_user_id),
                "title": "Not mine",
                "created_date": datetime(2026, 7, 3, tzinfo=timezone.utc),
            },
        ]
    )

    monkeypatch.setattr(ticket, "projects_collection", projects)
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    grant_project_role(project_id, current_user_id, "member")

    response = ticket.get_tickets(
        project_key,
        page=1,
        page_size=10,
        status=None,
        priority=None,
        category=None,
        assigned_agent=None,
        search=None,
        overdue_only=None,
        sort_by="created_at",
        sort_order="desc",
        current_user={"_id": current_user_id, "role": "agent"},
    )

    returned_codes = {item["ticket_code"] for item in response["data"]}
    assert returned_codes == {"TKT-1", "TKT-2", "TKT-3"}
    assert {item["issue_type"] for item in response["data"]} == {"task"}
    assert {tuple(item["labels"]) for item in response["data"]} == {()}
    assert response["pagination"]["total"] == 3


@pytest.mark.parametrize(
    "issue_type, expected_ticket_code",
    [
        (IssueType.BUG, "SUPPORT-1"),
        (IssueType.TASK, "SUPPORT-3"),
    ],
)
def test_get_tickets_filters_by_issue_type(
    monkeypatch,
    fake_collection,
    grant_project_role,
    issue_type,
    expected_ticket_code,
):
    project_key = "SUPPORT"
    project_id = ObjectId()
    current_user_id = ObjectId()
    projects = fake_collection.__class__([{"_id": project_id, "code": project_key}])
    tickets = fake_collection.__class__(
        [
            {
                "_id": ObjectId(),
                "project_id": str(project_id),
                "ticket_code": "SUPPORT-1",
                "issue_type": "bug",
                "title": "Broken printer",
                "created_date": datetime(2026, 7, 1, tzinfo=timezone.utc),
            },
            {
                "_id": ObjectId(),
                "project_id": str(project_id),
                "ticket_code": "SUPPORT-2",
                "issue_type": "story",
                "title": "Printer improvements",
                "created_date": datetime(2026, 7, 2, tzinfo=timezone.utc),
            },
            {
                "_id": ObjectId(),
                "project_id": str(project_id),
                "ticket_code": "SUPPORT-3",
                "title": "Legacy ticket without an issue type",
                "created_date": datetime(2026, 7, 3, tzinfo=timezone.utc),
            },
        ]
    )
    monkeypatch.setattr(ticket, "projects_collection", projects)
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    grant_project_role(project_id, current_user_id, "member")

    response = ticket.get_tickets(
        project_key,
        page=1,
        page_size=10,
        status=None,
        priority=None,
        category=None,
        issue_type=issue_type,
        assigned_agent=None,
        search=None,
        overdue_only=None,
        sort_by="created_at",
        sort_order="desc",
        current_user={"_id": current_user_id, "role": "agent"},
    )

    assert [item["ticket_code"] for item in response["data"]] == [expected_ticket_code]
    assert response["data"][0]["issue_type"] == issue_type.value
    assert response["pagination"]["total"] == 1
    assert response["filters_applied"]["issue_type"] == issue_type.value


def test_get_tickets_filters_by_all_labels(
    monkeypatch, fake_collection, grant_project_role
):
    project_key = "SUPPORT"
    project_id = ObjectId()
    current_user_id = ObjectId()
    projects = fake_collection.__class__([{"_id": project_id, "code": project_key}])
    tickets = fake_collection.__class__(
        [
            {
                "_id": ObjectId(),
                "project_id": str(project_id),
                "ticket_code": "SUPPORT-1",
                "labels": ["backend", "urgent"],
                "created_date": datetime(2026, 7, 1, tzinfo=timezone.utc),
            },
            {
                "_id": ObjectId(),
                "project_id": str(project_id),
                "ticket_code": "SUPPORT-2",
                "labels": ["backend"],
                "created_date": datetime(2026, 7, 2, tzinfo=timezone.utc),
            },
            {
                "_id": ObjectId(),
                "project_id": str(project_id),
                "ticket_code": "SUPPORT-3",
                "labels": ["urgent", "customer"],
                "created_date": datetime(2026, 7, 3, tzinfo=timezone.utc),
            },
        ]
    )
    monkeypatch.setattr(ticket, "projects_collection", projects)
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    grant_project_role(project_id, current_user_id, "member")

    response = ticket.get_tickets(
        project_key,
        page=1,
        page_size=10,
        status=None,
        priority=None,
        category=None,
        issue_type=None,
        labels=["backend", "urgent"],
        assigned_agent=None,
        search=None,
        overdue_only=None,
        sort_by="created_at",
        sort_order="desc",
        current_user={"_id": current_user_id, "role": "agent"},
    )

    assert [item["ticket_code"] for item in response["data"]] == ["SUPPORT-1"]
    assert response["pagination"]["total"] == 1
    assert response["filters_applied"]["labels"] == ["backend", "urgent"]


def test_ticket_list_exposes_issue_type_filter_in_openapi():
    openapi = app.openapi()
    parameters = openapi["paths"]["/tickets/{project_key}"]["get"]["parameters"]
    issue_type_parameter = next(
        parameter for parameter in parameters if parameter["name"] == "issue_type"
    )
    labels_parameter = next(
        parameter for parameter in parameters if parameter["name"] == "labels"
    )

    assert issue_type_parameter["in"] == "query"
    assert labels_parameter["in"] == "query"
    assert "/epics/{epic_id}" in openapi["paths"]
    assert openapi["components"]["schemas"]["IssueType"]["enum"] == [
        "bug",
        "task",
        "story",
        "epic",
        "subtask",
    ]


def test_get_tickets_handles_naive_due_date(
    monkeypatch, fake_collection, grant_project_role
):
    project_key = "SUPPORT"
    current_user_id = ObjectId()

    project_id = ObjectId()
    projects = fake_collection.__class__([{"_id": project_id, "code": project_key}])
    tickets = fake_collection.__class__(
        [
            {
                "_id": ObjectId(),
                "project_id": str(project_id),
                "project_key": project_key,
                "ticket_code": "TKT-1",
                "userId": str(current_user_id),
                "assigned_agent": None,
                "title": "Naive due date",
                "current_status_name": "open",
                "created_date": datetime(2026, 7, 1),
                "due_date": datetime(2020, 1, 1),
            }
        ]
    )

    monkeypatch.setattr(ticket, "projects_collection", projects)
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    grant_project_role(project_id, current_user_id, "member")

    response = ticket.get_tickets(
        project_key,
        page=1,
        page_size=10,
        status=None,
        priority=None,
        category=None,
        assigned_agent=None,
        search=None,
        overdue_only=None,
        sort_by="created_at",
        sort_order="desc",
        current_user={"_id": current_user_id, "role": "agent"},
    )

    assert response["data"][0]["is_overdue"] is True


def test_assign_ticket_allows_project_member_self_assign(
    monkeypatch, fake_collection, grant_project_role
):
    ticket_id = ObjectId()
    project_id = ObjectId()
    agent_id = ObjectId()
    tickets = fake_collection.__class__(
        [
            {
                "_id": ticket_id,
                "project_id": str(project_id),
                "assigned_agent": None,
                "userId": "user",
            }
        ]
    )
    users = fake_collection.__class__(
        [{"_id": agent_id, "role": "agent", "is_active": True}]
    )
    notifications = fake_collection.__class__()
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "user_collection", users)
    monkeypatch.setattr(ticket, "notifications_collection", notifications)
    grant_project_role(project_id, agent_id, "member")

    response = ticket.assign_ticket(
        str(ticket_id),
        AssignAgent(agent_id=str(agent_id)),
        current_user={"_id": agent_id, "role": "agent"},
    )

    assert response == {"message": "Ticket assigned successfully"}
    assert tickets.documents[0]["assigned_agent"] == str(agent_id)
    assert len(notifications.documents) == 1


def test_assign_ticket_succeeds(monkeypatch, fake_collection, grant_project_role):
    ticket_id = ObjectId()
    project_id = ObjectId()
    agent_id = ObjectId()
    lead_id = ObjectId()
    tickets = fake_collection.__class__(
        [
            {
                "_id": ticket_id,
                "project_id": str(project_id),
                "assigned_agent": None,
                "userId": "user",
            }
        ]
    )
    users = fake_collection.__class__(
        [{"_id": agent_id, "role": "agent", "is_active": True}]
    )
    notifications = fake_collection.__class__()
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "user_collection", users)
    monkeypatch.setattr(ticket, "notifications_collection", notifications)
    grant_project_role(project_id, lead_id)
    grant_project_role(project_id, agent_id, "member")

    response = ticket.assign_ticket(
        str(ticket_id),
        AssignAgent(agent_id=str(agent_id)),
        current_user={"_id": lead_id, "role": "super_admin"},
    )

    assert response == {"message": "Ticket assigned successfully"}
    assert tickets.documents[0]["assigned_agent"] == str(agent_id)
    assert len(notifications.documents) == 1


def test_assign_ticket_rejects_missing_ticket(monkeypatch, fake_collection):
    monkeypatch.setattr(ticket, "ticket_collection", fake_collection)
    monkeypatch.setattr(ticket, "user_collection", fake_collection.__class__())

    with pytest.raises(HTTPException) as exc_info:
        ticket.assign_ticket(
            str(ObjectId()),
            AssignAgent(agent_id=str(ObjectId())),
            current_user={"_id": ObjectId(), "role": "super_admin"},
        )

    assert exc_info.value.status_code == 404


def test_assign_ticket_rejects_missing_agent(
    monkeypatch, fake_collection, grant_project_role
):
    ticket_id = ObjectId()
    project_id = ObjectId()
    lead_id = ObjectId()
    tickets = fake_collection.__class__(
        [
            {
                "_id": ticket_id,
                "project_id": str(project_id),
                "assigned_agent": None,
                "userId": "user",
            }
        ]
    )
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "user_collection", fake_collection.__class__())
    grant_project_role(project_id, lead_id)

    with pytest.raises(HTTPException) as exc_info:
        ticket.assign_ticket(
            str(ticket_id),
            AssignAgent(agent_id=str(ObjectId())),
            current_user={"_id": lead_id, "role": "super_admin"},
        )

    assert exc_info.value.status_code == 404


def test_assign_ticket_rejects_target_user_without_working_project_role(
    monkeypatch, fake_collection, grant_project_role
):
    ticket_id = ObjectId()
    project_id = ObjectId()
    user_id = ObjectId()
    lead_id = ObjectId()
    tickets = fake_collection.__class__(
        [
            {
                "_id": ticket_id,
                "project_id": str(project_id),
                "assigned_agent": None,
                "userId": "user",
            }
        ]
    )
    users = fake_collection.__class__(
        [{"_id": user_id, "role": "admin", "is_active": True}]
    )
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "user_collection", users)
    grant_project_role(project_id, lead_id)

    with pytest.raises(HTTPException) as exc_info:
        ticket.assign_ticket(
            str(ticket_id),
            AssignAgent(agent_id=str(user_id)),
            current_user={"_id": lead_id, "role": "super_admin"},
        )

    assert exc_info.value.status_code == 400


def test_assign_ticket_allows_project_lead_reassignment(
    monkeypatch, fake_collection, grant_project_role
):
    ticket_id = ObjectId()
    project_id = ObjectId()
    old_agent_id = ObjectId()
    new_agent_id = ObjectId()
    admin_id = ObjectId()
    tickets = fake_collection.__class__(
        [
            {
                "_id": ticket_id,
                "project_id": str(project_id),
                "assigned_agent": str(old_agent_id),
                "userId": "user",
            }
        ]
    )
    users = fake_collection.__class__(
        [
            {"_id": old_agent_id, "role": "agent", "is_active": True},
            {"_id": new_agent_id, "role": "agent", "is_active": True},
        ]
    )
    notifications = fake_collection.__class__()
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "user_collection", users)
    monkeypatch.setattr(ticket, "notifications_collection", notifications)
    grant_project_role(project_id, admin_id)
    grant_project_role(project_id, new_agent_id, "member")

    response = ticket.assign_ticket(
        str(ticket_id),
        AssignAgent(agent_id=str(new_agent_id)),
        current_user={"_id": admin_id, "role": "super_admin"},
    )

    assert response == {"message": "Ticket assigned successfully"}
    assert tickets.documents[0]["assigned_agent"] == str(new_agent_id)
    assert len(notifications.documents) == 1
