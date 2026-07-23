from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.api import project, reports
from app.models.project import ProjectUpdate
from app.models.project_member import ProjectMemberCreate
from app.models.transition import (
    ProjectWorkflowUpdate,
    TransitionCreate,
    TransitionGuard,
)


class AggregateCollection:
    def __init__(self, results):
        self.results = results
        self.pipeline = None

    def aggregate(self, pipeline):
        self.pipeline = pipeline
        return self.results


def _admin():
    return {"_id": ObjectId(), "role": "admin"}


def _super_admin():
    return {"_id": ObjectId(), "role": "super_admin"}


def test_report_date_helpers_cover_closed_and_missing_values():
    closed_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
    naive = datetime(2026, 7, 2, 12, 30)

    assert reports._is_open_ticket({"closed_date": closed_at}) is False
    assert reports._as_aware_utc(naive) == naive.replace(tzinfo=timezone.utc)
    assert reports._ticket_recency({}) == datetime.min.replace(tzinfo=timezone.utc)
    assert reports._sprint_recency({}) == datetime.min.replace(tzinfo=timezone.utc)


def test_project_summary_includes_active_sprint_metrics(
    monkeypatch,
    fake_collection,
):
    project_id = ObjectId()
    sprint_id = ObjectId()
    now = datetime.now(timezone.utc)
    projects = fake_collection.__class__(
        [{"_id": project_id, "code": "SUM", "name": "Summary"}]
    )
    sprints = fake_collection.__class__(
        [
            {
                "_id": sprint_id,
                "project_id": project_id,
                "status": "active",
                "name": "Active",
                "start_date": now - timedelta(days=2),
                "end_date": now + timedelta(days=5),
            }
        ]
    )
    tickets = fake_collection.__class__(
        [
            {
                "_id": ObjectId(),
                "project_id": str(project_id),
                "sprint_id": str(sprint_id),
                "current_status_name": "In Progress",
                "story_points": 3,
            },
            {
                "_id": ObjectId(),
                "project_id": str(project_id),
                "sprint_id": str(sprint_id),
                "current_status_name": "Done",
                "story_points": 5,
            },
            {
                "_id": ObjectId(),
                "project_id": str(project_id),
                "sprint_id": None,
                "current_status_name": "Open",
                "story_points": 2,
            },
        ]
    )
    monkeypatch.setattr(reports, "projects_collection", projects)
    monkeypatch.setattr(reports, "sprints_collection", sprints)
    monkeypatch.setattr(reports, "ticket_collection", tickets)

    response = reports.report_project_summary(
        str(project_id),
        current_user=_admin(),
    )

    assert response["agile"]["active_sprint"] == {
        "sprint_id": str(sprint_id),
        "name": "Active",
        "start_date": now - timedelta(days=2),
        "end_date": now + timedelta(days=5),
        "total_tickets": 2,
        "completed_tickets": 1,
        "committed_story_points": 8,
        "completed_story_points": 5,
    }


@pytest.mark.parametrize(
    "endpoint",
    [reports.report_project_summary, reports.report_project_velocity],
)
def test_project_reports_return_404_for_missing_project(
    endpoint,
    monkeypatch,
    fake_collection,
):
    monkeypatch.setattr(reports, "projects_collection", fake_collection)

    with pytest.raises(HTTPException) as exc_info:
        endpoint(str(ObjectId()), current_user=_admin())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Project not found"


@pytest.mark.parametrize(
    ("velocities", "expected_trend", "expected_delta"),
    [
        ((3, 8), "up", 5),
        ((8, 3), "down", -5),
    ],
)
def test_project_velocity_reports_up_and_down_trends(
    velocities,
    expected_trend,
    expected_delta,
    monkeypatch,
    fake_collection,
):
    project_id = ObjectId()
    older = datetime(2026, 6, 1, tzinfo=timezone.utc)
    projects = fake_collection.__class__(
        [{"_id": project_id, "code": "VEL", "name": "Velocity"}]
    )
    sprints = fake_collection.__class__(
        [
            {
                "_id": ObjectId(),
                "project_id": project_id,
                "status": "completed",
                "name": "Older",
                "completed_at": older,
                "metrics": {
                    "committed_story_points": 10,
                    "completed_story_points": velocities[0],
                },
            },
            {
                "_id": ObjectId(),
                "project_id": project_id,
                "status": "completed",
                "name": "Newer",
                "completed_at": older + timedelta(days=14),
                "metrics": {
                    "committed_story_points": 10,
                    "completed_story_points": velocities[1],
                },
            },
        ]
    )
    monkeypatch.setattr(reports, "projects_collection", projects)
    monkeypatch.setattr(reports, "sprints_collection", sprints)

    response = reports.report_project_velocity(
        str(project_id),
        current_user=_admin(),
    )

    assert response["trend"] == expected_trend
    assert response["velocity_delta"] == expected_delta


def test_agent_ticket_report_enriches_aggregate_results(
    monkeypatch,
    fake_collection,
):
    agent_id = ObjectId()
    tickets = AggregateCollection(
        [
            {
                "_id": agent_id,
                "total_tickets": 5,
                "open_tickets": 1,
                "in_progress_tickets": 1,
                "on_hold_tickets": 1,
                "resolved_tickets": 1,
                "closed_tickets": 1,
            }
        ]
    )
    users = fake_collection.__class__([{"_id": agent_id, "name": "Ada Agent"}])
    monkeypatch.setattr(reports, "ticket_collection", tickets)
    monkeypatch.setattr(reports, "user_collection", users)

    response = reports.report_agent_tickets(current_user=_admin())

    assert response["data"][0]["agent_name"] == "Ada Agent"
    assert response["data"][0]["total_tickets"] == 5
    assert tickets.pipeline[-1] == {"$sort": {"total_tickets": -1}}


def test_overdue_report_counts_priorities_and_assignees(monkeypatch):
    agent_id = str(ObjectId())
    tickets = AggregateCollection(
        [
            {
                "total_overdue": 4,
                "by_priority": [
                    {"priority": "high"},
                    {"priority": "high"},
                    {},
                ],
                "by_agent": [
                    {"agent": agent_id},
                    {"agent": agent_id},
                    {"agent": None},
                    {},
                ],
            }
        ]
    )
    monkeypatch.setattr(reports, "ticket_collection", tickets)

    response = reports.report_overdue_tickets(current_user=_admin())

    assert response == {
        "total_overdue": 4,
        "by_priority": {"high": 2, "unknown": 1},
        "by_agent": {agent_id: 2, "unassigned": 2},
    }


def test_average_resolution_report_handles_empty_and_grouped_results(
    monkeypatch,
):
    tickets = AggregateCollection([])
    monkeypatch.setattr(reports, "ticket_collection", tickets)

    assert reports.report_avg_resolution_time(current_user=_admin()) == {
        "avg_resolution_hours": 0,
        "min_resolution_hours": 0,
        "max_resolution_hours": 0,
        "total_resolved": 0,
        "by_priority": {},
    }

    tickets.results = [
        {
            "avg_resolution_hours": 3.456,
            "min_resolution_hours": 1.234,
            "max_resolution_hours": 5.678,
            "total_resolved": 3,
            "by_priority": [
                {"priority": "high", "resolution_time": 2.0},
                {"priority": "high", "resolution_time": 4.0},
                {"priority": "low", "resolution_time": 1.5},
            ],
        }
    ]

    assert reports.report_avg_resolution_time(current_user=_admin()) == {
        "avg_resolution_hours": 3.46,
        "min_resolution_hours": 1.23,
        "max_resolution_hours": 5.68,
        "total_resolved": 3,
        "by_priority": {"high": 3.0, "low": 1.5},
    }


def test_project_list_skips_invalid_empty_and_duplicate_memberships(
    monkeypatch,
    fake_collection,
    project_members,
):
    user_id = ObjectId()
    visible_project_id = ObjectId()
    hidden_project_id = ObjectId()
    projects = fake_collection.__class__(
        [
            {"_id": visible_project_id, "code": "YES"},
            {"_id": hidden_project_id, "code": "NO"},
        ]
    )
    monkeypatch.setattr(project, "projects_collection", projects)
    monkeypatch.setattr(
        project,
        "ticket_collection",
        fake_collection.__class__(),
    )
    project_members.insert_one(
        {
            "project_id": hidden_project_id,
            "user_id": user_id,
            "role": "observer",
        }
    )
    project_members.insert_one({"user_id": user_id, "role": "member"})
    project_members.insert_one(
        {
            "project_id": visible_project_id,
            "user_id": user_id,
            "role": "member",
        }
    )
    project_members.insert_one(
        {
            "project_id": visible_project_id,
            "user_id": user_id,
            "role": "reporter",
        }
    )

    response = project.get_projects(current_user={"_id": user_id, "role": "admin"})

    assert [item["code"] for item in response["projects"]] == ["YES"]


def test_get_project_workflow_serializes_ordered_transitions(
    monkeypatch,
    fake_collection,
    transitions,
):
    project_id = ObjectId()
    source_id = ObjectId()
    target_id = ObjectId()
    monkeypatch.setattr(
        project,
        "projects_collection",
        fake_collection.__class__([{"_id": project_id, "code": "FLOW"}]),
    )
    transitions.insert_one(
        {
            "_id": ObjectId(),
            "project_id": project_id,
            "name": "Second",
            "from_status_ids": [str(target_id)],
            "to_status_id": str(source_id),
            "order": 2,
        }
    )
    transitions.insert_one(
        {
            "_id": ObjectId(),
            "project_id": project_id,
            "name": "First",
            "from_status_ids": [str(source_id)],
            "to_status_id": str(target_id),
            "order": 1,
        }
    )

    response = project.get_project_workflow(
        str(project_id),
        current_user=_super_admin(),
    )

    assert [item["name"] for item in response["transitions"]] == [
        "First",
        "Second",
    ]
    assert response["transitions"][0]["project_id"] == str(project_id)


@pytest.mark.parametrize(
    ("case", "expected_status", "expected_detail"),
    [
        ("duplicate_source", 400, "source status IDs must be unique"),
        ("unassigned_source", 400, "source status is not assigned"),
        ("unassigned_target", 400, "target status is not assigned"),
        ("missing_status", 404, "Workflow status"),
    ],
)
def test_configure_workflow_rejects_invalid_status_references(
    case,
    expected_status,
    expected_detail,
    monkeypatch,
    fake_collection,
):
    project_id = ObjectId()
    source_id = ObjectId()
    target_id = ObjectId()
    unassigned_id = ObjectId()

    if case == "duplicate_source":
        assigned_statuses = [str(source_id), str(target_id)]
        transition = TransitionCreate.model_construct(
            name="Invalid",
            from_status_ids=[str(source_id), str(source_id)],
            to_status_id=str(target_id),
            guard=TransitionGuard(),
        )
    elif case == "unassigned_source":
        assigned_statuses = [str(target_id)]
        transition = TransitionCreate(
            name="Invalid",
            from_status_ids=[str(unassigned_id)],
            to_status_id=str(target_id),
        )
    elif case == "unassigned_target":
        assigned_statuses = [str(source_id)]
        transition = TransitionCreate(
            name="Invalid",
            from_status_ids=[str(source_id)],
            to_status_id=str(unassigned_id),
        )
    else:
        assigned_statuses = [str(source_id), str(target_id)]
        transition = TransitionCreate(
            name="Invalid",
            from_status_ids=[str(source_id)],
            to_status_id=str(target_id),
        )

    monkeypatch.setattr(
        project,
        "projects_collection",
        fake_collection.__class__(
            [
                {
                    "_id": project_id,
                    "assigned_statuses": assigned_statuses,
                }
            ]
        ),
    )
    monkeypatch.setattr(
        project,
        "statuses_collection",
        fake_collection.__class__(),
    )
    payload = ProjectWorkflowUpdate.model_construct(transitions=[transition])

    with pytest.raises(HTTPException) as exc_info:
        project.configure_project_workflow(
            str(project_id),
            payload,
            current_user=_super_admin(),
        )

    assert exc_info.value.status_code == expected_status
    assert expected_detail in exc_info.value.detail


def test_update_project_rejects_an_empty_payload(
    monkeypatch,
    fake_collection,
):
    project_id = ObjectId()
    monkeypatch.setattr(
        project,
        "projects_collection",
        fake_collection.__class__([{"_id": project_id, "code": "EMPTY"}]),
    )

    with pytest.raises(HTTPException) as exc_info:
        project.update_project(
            ProjectUpdate(),
            str(project_id),
            current_user=_super_admin(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "No project fields supplied"


def test_add_project_member_rejects_missing_and_existing_users(
    monkeypatch,
    fake_collection,
    project_members,
):
    project_id = ObjectId()
    member_id = ObjectId()
    users = fake_collection.__class__()
    monkeypatch.setattr(
        project,
        "projects_collection",
        fake_collection.__class__([{"_id": project_id, "code": "MEM"}]),
    )
    monkeypatch.setattr(project, "user_collection", users)
    member = ProjectMemberCreate(user_id=str(member_id), role="member")

    with pytest.raises(HTTPException) as missing_error:
        project.add_project_member(
            str(project_id),
            member,
            current_user=_super_admin(),
        )

    assert missing_error.value.status_code == 404

    users.insert_one({"_id": member_id, "name": "Existing"})
    project_members.insert_one(
        {
            "project_id": project_id,
            "user_id": member_id,
            "role": "member",
        }
    )

    with pytest.raises(HTTPException) as duplicate_error:
        project.add_project_member(
            str(project_id),
            member,
            current_user=_super_admin(),
        )

    assert duplicate_error.value.status_code == 409


def test_get_project_members_skips_deleted_users(
    monkeypatch,
    fake_collection,
    project_members,
):
    project_id = ObjectId()
    active_user_id = ObjectId()
    deleted_user_id = ObjectId()
    monkeypatch.setattr(
        project,
        "projects_collection",
        fake_collection.__class__([{"_id": project_id, "code": "MEM"}]),
    )
    monkeypatch.setattr(
        project,
        "user_collection",
        fake_collection.__class__(
            [
                {
                    "_id": active_user_id,
                    "name": "Active User",
                    "email": "active@example.test",
                }
            ]
        ),
    )
    project_members.insert_one(
        {
            "project_id": project_id,
            "user_id": deleted_user_id,
            "role": "member",
        }
    )
    project_members.insert_one(
        {
            "project_id": project_id,
            "user_id": active_user_id,
            "role": "reporter",
        }
    )

    response = project.get_project_members(
        str(project_id),
        current_user=_super_admin(),
    )

    assert response == [
        {
            "user_id": str(active_user_id),
            "name": "Active User",
            "email": "active@example.test",
            "role": "reporter",
        }
    ]
