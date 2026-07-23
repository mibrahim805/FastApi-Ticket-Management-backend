from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.api import reports, ticket
from app.main import app


def test_my_dashboard_is_scoped_to_current_user_and_accessible_projects(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    first_project_id = ObjectId()
    second_project_id = ObjectId()
    inaccessible_project_id = ObjectId()
    user_id = ObjectId()
    other_user_id = ObjectId()
    now = datetime.now(timezone.utc)
    grant_project_role(first_project_id, user_id, "member")
    grant_project_role(second_project_id, user_id, "reporter")
    tickets = fake_collection.__class__(
        [
            {
                "_id": ObjectId(),
                "project_id": str(first_project_id),
                "ticket_code": "ONE-1",
                "title": "Created by me",
                "userId": str(user_id),
                "assigned_agent": None,
                "current_status_name": "Open",
                "created_date": now - timedelta(days=2),
            },
            {
                "_id": ObjectId(),
                "project_id": str(first_project_id),
                "ticket_code": "ONE-2",
                "title": "Assigned to me",
                "userId": str(other_user_id),
                "assigned_agent": str(user_id),
                "current_status_name": "In Progress",
                "due_date": now - timedelta(days=1),
                "created_date": now - timedelta(days=1),
            },
            {
                "_id": ObjectId(),
                "project_id": str(first_project_id),
                "ticket_code": "ONE-3",
                "title": "Resolved work",
                "userId": str(user_id),
                "assigned_agent": str(user_id),
                "current_status_name": "Resolved",
                "created_date": now,
            },
            {
                "_id": ObjectId(),
                "project_id": str(second_project_id),
                "ticket_code": "TWO-1",
                "title": "Reopened work",
                "userId": str(user_id),
                "assigned_agent": str(other_user_id),
                "current_status_name": "Reopened",
                "created_date": now,
            },
            {
                "_id": ObjectId(),
                "project_id": str(inaccessible_project_id),
                "ticket_code": "SECRET-1",
                "title": "Inaccessible assignment",
                "userId": str(other_user_id),
                "assigned_agent": str(user_id),
                "current_status_name": "Open",
                "created_date": now,
            },
        ]
    )
    monkeypatch.setattr(reports, "ticket_collection", tickets)

    response = reports.get_my_dashboard(
        limit=1,
        current_user={"_id": user_id, "role": "agent"},
    )

    assert response["project_count"] == 2
    assert response["counts"] == {
        "my_open_issues": 2,
        "assigned_to_me": 1,
        "overdue_assigned_to_me": 1,
    }
    assert [item["ticket_code"] for item in response["my_open_issues"]] == ["TWO-1"]
    assert [item["ticket_code"] for item in response["assigned_to_me"]] == ["ONE-2"]
    assert response["assigned_to_me"][0]["is_overdue"] is True


def test_my_dashboard_is_empty_without_accessible_projects(
    monkeypatch,
    fake_collection,
):
    monkeypatch.setattr(reports, "ticket_collection", fake_collection)
    user_id = ObjectId()

    response = reports.get_my_dashboard(
        current_user={"_id": user_id, "role": "agent"},
    )

    assert response["user_id"] == str(user_id)
    assert response["project_count"] == 0
    assert response["my_open_issues"] == []
    assert response["assigned_to_me"] == []


def test_project_summary_uses_dynamic_status_and_priority_buckets(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    user_id = ObjectId()
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        reports,
        "projects_collection",
        fake_collection.__class__(
            [{"_id": project_id, "code": "OPS", "name": "Operations"}]
        ),
    )
    monkeypatch.setattr(
        reports,
        "ticket_collection",
        fake_collection.__class__(
            [
                {
                    "_id": ObjectId(),
                    "project_id": str(project_id),
                    "current_status_name": "In Development",
                    "priority": "High",
                    "assigned_agent": str(ObjectId()),
                    "due_date": now - timedelta(hours=1),
                },
                {
                    "_id": ObjectId(),
                    "project_id": str(project_id),
                    "current_status_name": "Closed",
                    "priority": "Low",
                    "assigned_agent": None,
                    "due_date": now - timedelta(days=2),
                },
                {
                    "_id": ObjectId(),
                    "project_id": str(project_id),
                    "current_status_name": "QA Review",
                    "priority": "High",
                    "assigned_agent": None,
                },
            ]
        ),
    )
    grant_project_role(project_id, user_id, "reporter")

    response = reports.report_project_summary(
        str(project_id),
        current_user={"_id": user_id, "role": "customer"},
    )

    assert response["project"]["code"] == "OPS"
    assert response["total_tickets"] == 3
    assert response["open_tickets"] == 2
    assert response["assigned_tickets"] == 1
    assert response["unassigned_tickets"] == 2
    assert response["overdue_tickets"] == 1
    assert response["by_status"] == {
        "in_development": 1,
        "closed": 1,
        "qa_review": 1,
    }
    assert response["by_priority"] == {"high": 2, "low": 1}


def test_non_member_cannot_view_project_summary(monkeypatch, fake_collection):
    project_id = ObjectId()
    monkeypatch.setattr(
        reports,
        "projects_collection",
        fake_collection.__class__([{"_id": project_id}]),
    )

    with pytest.raises(HTTPException) as exc_info:
        reports.report_project_summary(
            str(project_id),
            current_user={"_id": ObjectId(), "role": "agent"},
        )

    assert exc_info.value.status_code == 403


def test_existing_ticket_summary_accepts_accessible_project_filter(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    user_id = ObjectId()
    projects = fake_collection.__class__([{"_id": project_id, "code": "ONE"}])
    tickets = fake_collection.__class__()
    captured_pipeline = []

    def aggregate(pipeline):
        captured_pipeline.extend(pipeline)
        return [{"total_tickets": 4}]

    tickets.aggregate = aggregate
    monkeypatch.setattr(ticket, "projects_collection", projects)
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    grant_project_role(project_id, user_id, "member")

    response = ticket.get_tickets_summary(
        project_id=str(project_id),
        current_user={"_id": user_id, "role": "agent"},
    )

    assert response == {"total_tickets": 4}
    assert captured_pipeline[0] == {
        "$match": {"project_id": {"$in": [str(project_id)]}}
    }


def test_reporting_dashboard_routes_are_exposed_in_openapi():
    paths = app.openapi()["paths"]
    summary_parameters = paths["/tickets_summary"]["get"]["parameters"]

    assert "/reports/my-dashboard" in paths
    assert "/reports/projects/{project_id}/summary" in paths
    assert "project_id" in {parameter["name"] for parameter in summary_parameters}
