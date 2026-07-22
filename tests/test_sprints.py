from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from fastapi import HTTPException
from pydantic import ValidationError

from app.api import reports, sprint
from app.main import app
from app.models.sprint import (
    SprintCreate,
    StoryPointsUpdate,
    TicketPositionUpdate,
    TicketSprintUpdate,
)


def _project(project_id, status_ids):
    return {
        "_id": project_id,
        "code": "AGL",
        "name": "Agile Delivery",
        "assigned_statuses": [str(status_id) for status_id in status_ids],
    }


def test_sprint_dates_and_story_points_are_validated():
    now = datetime.now(timezone.utc)

    with pytest.raises(ValidationError):
        SprintCreate(
            name="Invalid",
            start_date=now,
            end_date=now - timedelta(days=1),
        )

    with pytest.raises(ValidationError):
        StoryPointsUpdate(story_points=-1)

    assert StoryPointsUpdate(story_points=None).story_points is None


def test_project_lead_can_create_and_start_only_one_sprint(
    monkeypatch,
    fake_collection,
    sprints,
    grant_project_role,
):
    project_id = ObjectId()
    lead_id = ObjectId()
    projects = fake_collection.__class__([_project(project_id, [])])
    monkeypatch.setattr(sprint, "projects_collection", projects)
    monkeypatch.setattr(sprint, "ticket_collection", fake_collection.__class__())
    grant_project_role(project_id, lead_id, "lead")
    current_user = {"_id": lead_id, "role": "agent"}

    first = sprint.create_sprint(
        str(project_id),
        SprintCreate(name="Sprint 1", goal="Ship the API"),
        current_user=current_user,
    )
    second = sprint.create_sprint(
        str(project_id),
        SprintCreate(name="Sprint 2"),
        current_user=current_user,
    )

    started = sprint.start_sprint(first["_id"], current_user=current_user)

    assert started["status"] == "active"
    assert started["start_date"].tzinfo is not None
    with pytest.raises(HTTPException) as exc_info:
        sprint.start_sprint(second["_id"], current_user=current_user)
    assert exc_info.value.status_code == 409
    assert "active sprint" in exc_info.value.detail

    listed = sprint.list_sprints(
        str(project_id),
        sprint_status=None,
        current_user=current_user,
    )
    assert [item["status"] for item in listed] == ["active", "planned"]
    assert "name_key" not in listed[0]
    assert len(sprints.documents) == 2


def test_only_project_lead_can_manage_sprint_lifecycle(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    member_id = ObjectId()
    monkeypatch.setattr(
        sprint,
        "projects_collection",
        fake_collection.__class__([_project(project_id, [])]),
    )
    grant_project_role(project_id, member_id, "member")

    with pytest.raises(HTTPException) as exc_info:
        sprint.create_sprint(
            str(project_id),
            SprintCreate(name="Forbidden"),
            current_user={"_id": member_id, "role": "agent"},
        )

    assert exc_info.value.status_code == 403


def test_ticket_estimation_assignment_and_drag_ordering(
    monkeypatch,
    fake_collection,
    sprints,
    grant_project_role,
):
    project_id = ObjectId()
    status_id = ObjectId()
    member_id = ObjectId()
    sprint_id = ObjectId()
    first_ticket_id = ObjectId()
    second_ticket_id = ObjectId()
    third_ticket_id = ObjectId()
    projects = fake_collection.__class__([_project(project_id, [status_id])])
    tickets = fake_collection.__class__(
        [
            {
                "_id": first_ticket_id,
                "project_id": str(project_id),
                "ticket_code": "AGL-1",
                "current_status_id": str(status_id),
                "sprint_id": None,
                "backlog_position": 0,
                "board_position": 0,
            },
            {
                "_id": second_ticket_id,
                "project_id": str(project_id),
                "ticket_code": "AGL-2",
                "current_status_id": str(status_id),
                "sprint_id": None,
                "backlog_position": 1,
                "board_position": 1,
            },
            {
                "_id": third_ticket_id,
                "project_id": str(project_id),
                "ticket_code": "AGL-3",
                "current_status_id": str(status_id),
                "sprint_id": str(sprint_id),
                "backlog_position": None,
                "board_position": 0,
            },
        ]
    )
    sprints.insert_one(
        {
            "_id": sprint_id,
            "project_id": project_id,
            "name": "Sprint 1",
            "name_key": "sprint 1",
            "status": "planned",
        }
    )
    monkeypatch.setattr(sprint, "projects_collection", projects)
    monkeypatch.setattr(sprint, "ticket_collection", tickets)
    grant_project_role(project_id, member_id, "member")
    current_user = {"_id": member_id, "role": "agent"}

    estimated = sprint.set_story_points(
        str(first_ticket_id),
        StoryPointsUpdate(story_points=8),
        current_user=current_user,
    )
    assigned = sprint.set_ticket_sprint(
        str(first_ticket_id),
        TicketSprintUpdate(sprint_id=str(sprint_id)),
        current_user=current_user,
    )
    reordered = sprint.reorder_board_ticket(
        str(first_ticket_id),
        TicketPositionUpdate(position=0),
        current_user=current_user,
    )

    assert estimated["story_points"] == 8
    assert assigned["sprint_id"] == str(sprint_id)
    assert assigned["backlog_position"] is None
    assert reordered["ordered_ticket_ids"] == [
        str(first_ticket_id),
        str(third_ticket_id),
    ]
    assert (
        next(
            ticket for ticket in tickets.documents if ticket["_id"] == second_ticket_id
        )["backlog_position"]
        == 0
    )

    moved_back = sprint.set_ticket_sprint(
        str(first_ticket_id),
        TicketSprintUpdate(sprint_id=None),
        current_user=current_user,
    )
    backlog_order = sprint.reorder_backlog_ticket(
        str(first_ticket_id),
        TicketPositionUpdate(position=0),
        current_user=current_user,
    )
    assert moved_back["sprint_id"] is None
    assert backlog_order["ordered_ticket_ids"] == [
        str(first_ticket_id),
        str(second_ticket_id),
    ]


def test_active_sprint_board_preserves_project_status_columns(
    monkeypatch,
    fake_collection,
    sprints,
    grant_project_role,
):
    project_id = ObjectId()
    todo_id = ObjectId()
    done_id = ObjectId()
    sprint_id = ObjectId()
    user_id = ObjectId()
    projects = fake_collection.__class__([_project(project_id, [todo_id, done_id])])
    statuses = fake_collection.__class__(
        [
            {"_id": todo_id, "name": "To Do"},
            {"_id": done_id, "name": "Done"},
        ]
    )
    tickets = fake_collection.__class__(
        [
            {
                "_id": ObjectId(),
                "project_id": str(project_id),
                "ticket_code": "AGL-2",
                "current_status_id": str(todo_id),
                "sprint_id": str(sprint_id),
                "board_position": 1,
            },
            {
                "_id": ObjectId(),
                "project_id": str(project_id),
                "ticket_code": "AGL-1",
                "current_status_id": str(todo_id),
                "sprint_id": str(sprint_id),
                "board_position": 0,
            },
        ]
    )
    sprints.insert_one(
        {
            "_id": sprint_id,
            "project_id": project_id,
            "name": "Sprint 1",
            "name_key": "sprint 1",
            "status": "active",
        }
    )
    monkeypatch.setattr(sprint, "projects_collection", projects)
    monkeypatch.setattr(sprint, "statuses_collection", statuses)
    monkeypatch.setattr(sprint, "ticket_collection", tickets)
    grant_project_role(project_id, user_id, "reporter")

    board = sprint.get_sprint_board(
        str(project_id),
        sprint_id=None,
        backlog=False,
        current_user={"_id": user_id, "role": "customer"},
    )

    assert board["scope"] == "sprint"
    assert [column["status_name"] for column in board["columns"]] == [
        "To Do",
        "Done",
    ]
    assert [ticket["ticket_code"] for ticket in board["columns"][0]["tickets"]] == [
        "AGL-1",
        "AGL-2",
    ]


def test_completing_sprint_snapshots_velocity_and_returns_incomplete_work(
    monkeypatch,
    fake_collection,
    sprints,
    grant_project_role,
):
    project_id = ObjectId()
    todo_id = ObjectId()
    done_id = ObjectId()
    sprint_id = ObjectId()
    lead_id = ObjectId()
    complete_ticket_id = ObjectId()
    incomplete_ticket_id = ObjectId()
    project_doc = _project(project_id, [todo_id, done_id])
    projects = fake_collection.__class__([project_doc])
    tickets = fake_collection.__class__(
        [
            {
                "_id": complete_ticket_id,
                "project_id": str(project_id),
                "ticket_code": "AGL-1",
                "current_status_id": str(done_id),
                "current_status_name": "Done",
                "sprint_id": str(sprint_id),
                "story_points": 5,
                "board_position": 0,
            },
            {
                "_id": incomplete_ticket_id,
                "project_id": str(project_id),
                "ticket_code": "AGL-2",
                "current_status_id": str(todo_id),
                "current_status_name": "To Do",
                "sprint_id": str(sprint_id),
                "story_points": 8,
                "board_position": 0,
            },
        ]
    )
    sprints.insert_one(
        {
            "_id": sprint_id,
            "project_id": project_id,
            "name": "Sprint 1",
            "name_key": "sprint 1",
            "status": "active",
            "created_at": datetime.now(timezone.utc) - timedelta(days=14),
        }
    )
    monkeypatch.setattr(sprint, "projects_collection", projects)
    monkeypatch.setattr(sprint, "ticket_collection", tickets)
    monkeypatch.setattr(reports, "projects_collection", projects)
    monkeypatch.setattr(reports, "ticket_collection", tickets)
    grant_project_role(project_id, lead_id, "lead")
    current_user = {"_id": lead_id, "role": "agent"}

    completed = sprint.complete_sprint(
        str(sprint_id),
        current_user=current_user,
    )

    assert completed["velocity"] == 5
    assert completed["sprint"]["metrics"] == {
        "committed_tickets": 2,
        "completed_tickets": 1,
        "incomplete_tickets": 1,
        "committed_story_points": 13,
        "completed_story_points": 5,
        "incomplete_story_points": 8,
        "unestimated_tickets": 0,
        "committed_unestimated_tickets": 0,
        "final_scope_tickets": 2,
        "final_scope_story_points": 13,
        "scope_change_tickets": 0,
        "scope_change_story_points": 0,
    }
    assert completed["moved_to_backlog"] == [str(incomplete_ticket_id)]
    incomplete_ticket = next(
        ticket for ticket in tickets.documents if ticket["_id"] == incomplete_ticket_id
    )
    assert incomplete_ticket["sprint_id"] is None
    assert incomplete_ticket["backlog_position"] == 0

    velocity = reports.report_project_velocity(
        str(project_id),
        limit=10,
        current_user=current_user,
    )
    summary = reports.report_project_summary(
        str(project_id),
        current_user=current_user,
    )
    assert velocity["average_velocity"] == 5.0
    assert velocity["latest_velocity"] == 5
    assert velocity["sprints"][0]["completion_rate"] == 38.46
    assert summary["agile"]["backlog_tickets"] == 1
    assert summary["agile"]["backlog_story_points"] == 8


def test_sprint_and_velocity_routes_are_exposed_in_openapi():
    paths = app.openapi()["paths"]

    assert "/projects/{project_id}/sprints" in paths
    assert "/projects/{project_id}/backlog" in paths
    assert "/projects/{project_id}/board" in paths
    assert "/sprints/{sprint_id}/start" in paths
    assert "/sprints/{sprint_id}/complete" in paths
    assert "/tickets/{ticket_id}/story-points" in paths
    assert "/tickets/{ticket_id}/sprint" in paths
    assert "/tickets/{ticket_id}/board-position" in paths
    assert "/reports/projects/{project_id}/velocity" in paths
