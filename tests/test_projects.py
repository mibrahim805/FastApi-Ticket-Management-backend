import asyncio

import pytest
from bson import ObjectId
from fastapi import HTTPException
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from app import main as app_main
from app.api import project, ticket
from app.db import db as database
from app.main import app
from app.models.project import ProjectCreate, ProjectUpdate
from app.models.project_member import ProjectMemberCreate
from app.models.ticket import Category, Priority, TicketCreate


@pytest.mark.parametrize("code", ["AB", "A1", "PROJECT10"])
def test_project_key_accepts_short_uppercase_alphanumeric_values(code):
    project = ProjectCreate(name="Example", code=code)

    assert project.code == code


@pytest.mark.parametrize(
    "code",
    ["A", "lower", "1ABC", "A-B", "ABCDEFGHIJK"],
)
def test_project_key_rejects_invalid_values(code):
    with pytest.raises(ValidationError):
        ProjectCreate(name="Example", code=code)


@pytest.mark.parametrize("code", ["lower", "1ABC", "A-B"])
def test_project_update_key_rejects_invalid_values(code):
    with pytest.raises(ValidationError):
        ProjectUpdate(code=code)


def test_database_creates_case_insensitive_unique_project_key_index(monkeypatch):
    project_calls = []
    user_calls = []
    issue_link_calls = []
    transition_calls = []
    notification_preference_calls = []
    saved_filter_calls = []
    rate_limit_calls = []
    reset_token_calls = []
    sprint_calls = []
    ticket_calls = []

    class RecordingCollection:
        def __init__(self, calls):
            self.calls = calls

        def create_index(self, keys, **options):
            self.calls.append((keys, options))

    monkeypatch.setattr(
        database,
        "projects_collection",
        RecordingCollection(project_calls),
    )
    monkeypatch.setattr(
        database,
        "user_collection",
        RecordingCollection(user_calls),
    )
    monkeypatch.setattr(
        database,
        "issue_links_collection",
        RecordingCollection(issue_link_calls),
    )
    monkeypatch.setattr(
        database,
        "transitions_collection",
        RecordingCollection(transition_calls),
    )
    monkeypatch.setattr(
        database,
        "notification_preferences_collection",
        RecordingCollection(notification_preference_calls),
    )
    monkeypatch.setattr(
        database,
        "saved_filters_collection",
        RecordingCollection(saved_filter_calls),
    )
    monkeypatch.setattr(
        database,
        "rate_limits_collection",
        RecordingCollection(rate_limit_calls),
    )
    monkeypatch.setattr(
        database,
        "reset_tokens_collection",
        RecordingCollection(reset_token_calls),
    )
    monkeypatch.setattr(
        database,
        "sprints_collection",
        RecordingCollection(sprint_calls),
    )
    monkeypatch.setattr(
        database,
        "ticket_collection",
        RecordingCollection(ticket_calls),
    )

    database.ensure_database_indexes()

    assert user_calls == [
        (
            [("role", 1)],
            {
                "name": "users_one_super_admin",
                "unique": True,
                "partialFilterExpression": {"role": "super_admin"},
            },
        )
    ]
    assert reset_token_calls == [
        (
            [("expires_at", 1)],
            {"name": "reset_tokens_expiry_ttl", "expireAfterSeconds": 0},
        )
    ]

    assert project_calls == [
        (
            [("code", 1)],
            {
                "name": "projects_code_unique",
                "unique": True,
                "collation": {"locale": "en", "strength": 2},
            },
        )
    ]
    assert issue_link_calls == [
        (
            [
                ("from_ticket_id", 1),
                ("to_ticket_id", 1),
                ("link_type", 1),
            ],
            {
                "name": "issue_links_from_to_type_unique",
                "unique": True,
            },
        ),
        (
            [("from_ticket_id", 1)],
            {"name": "issue_links_from_ticket"},
        ),
        (
            [("to_ticket_id", 1)],
            {"name": "issue_links_to_ticket"},
        ),
    ]
    assert transition_calls == [
        (
            [("project_id", 1), ("name", 1)],
            {"name": "transitions_project_name"},
        ),
        (
            [("project_id", 1), ("from_status_ids", 1)],
            {"name": "transitions_project_source"},
        ),
    ]
    assert notification_preference_calls == [
        (
            [("user_id", 1), ("project_id", 1)],
            {
                "name": "notification_preferences_user_project_unique",
                "unique": True,
            },
        )
    ]
    assert saved_filter_calls == [
        (
            [("user_id", 1), ("name_key", 1)],
            {
                "name": "saved_filters_user_name_unique",
                "unique": True,
            },
        )
    ]
    assert rate_limit_calls == [
        (
            [("expires_at", 1)],
            {
                "name": "rate_limits_expiry_ttl",
                "expireAfterSeconds": 0,
            },
        )
    ]
    assert sprint_calls == [
        (
            [("project_id", 1), ("name_key", 1)],
            {
                "name": "sprints_project_name_unique",
                "unique": True,
            },
        ),
        (
            [("project_id", 1), ("status", 1)],
            {
                "name": "sprints_one_active_per_project",
                "unique": True,
                "partialFilterExpression": {"status": "active"},
            },
        ),
    ]
    assert ticket_calls == [
        (
            [
                ("project_id", 1),
                ("sprint_id", 1),
                ("current_status_id", 1),
                ("board_position", 1),
            ],
            {"name": "tickets_sprint_status_position"},
        ),
        (
            [
                ("project_id", 1),
                ("sprint_id", 1),
                ("backlog_position", 1),
            ],
            {"name": "tickets_project_backlog_position"},
        ),
    ]


def test_application_startup_ensures_database_indexes(monkeypatch):
    calls = []
    monkeypatch.setattr(
        app_main,
        "ensure_database_indexes",
        lambda: calls.append("created"),
    )
    monkeypatch.setattr(
        app_main,
        "migrate_legacy_global_roles",
        lambda: calls.append("roles_migrated"),
    )
    monkeypatch.setattr(
        app_main,
        "migrate_legacy_workflow_transitions",
        lambda: calls.append("migrated"),
    )

    async def run_lifespan():
        async with app_main.lifespan(app_main.app):
            pass

    asyncio.run(run_lifespan())

    assert calls == ["created", "roles_migrated", "migrated"]


def test_legacy_inert_global_roles_are_removed(monkeypatch, fake_collection):
    ordinary_ids = [ObjectId(), ObjectId(), ObjectId()]
    admin_id = ObjectId()
    users = fake_collection.__class__(
        [
            {"_id": ordinary_ids[0], "role": "user"},
            {"_id": ordinary_ids[1], "role": "customer"},
            {"_id": ordinary_ids[2], "role": "agent"},
            {"_id": admin_id, "role": "admin"},
        ]
    )
    monkeypatch.setattr(database, "user_collection", users)

    assert database.migrate_legacy_global_roles() == 3
    assert all(
        "role" not in users.find_one({"_id": user_id}) for user_id in ordinary_ids
    )
    assert users.find_one({"_id": admin_id})["role"] == "admin"


def test_legacy_embedded_workflow_is_migrated(
    monkeypatch,
    fake_collection,
):
    project_id = ObjectId()
    source_id = ObjectId()
    target_id = ObjectId()
    projects = fake_collection.__class__(
        [
            {
                "_id": project_id,
                "created_by": str(ObjectId()),
                "workflow_transitions": [
                    {
                        "name": "Complete",
                        "from_status_id": str(source_id),
                        "to_status_id": str(target_id),
                        "guards": {
                            "allowed_roles": ["member"],
                            "requires_assignee": True,
                        },
                    }
                ],
            }
        ]
    )
    transitions = fake_collection.__class__()
    monkeypatch.setattr(database, "projects_collection", projects)
    monkeypatch.setattr(database, "transitions_collection", transitions)

    migrated = database.migrate_legacy_workflow_transitions()

    assert migrated == 1
    assert transitions.documents[0]["project_id"] == project_id
    assert transitions.documents[0]["from_status_ids"] == [str(source_id)]
    assert transitions.documents[0]["guard"]["requires_assignee"] is True
    assert "workflow_transitions" not in projects.documents[0]


def test_create_project_rejects_duplicate_key(
    monkeypatch, fake_collection, project_members
):
    projects = fake_collection.__class__([{"_id": ObjectId(), "code": "help"}])
    monkeypatch.setattr(project, "projects_collection", projects)

    with pytest.raises(HTTPException) as exc_info:
        project.create_project(
            ProjectCreate(name="Duplicate", code="HELP"),
            current_user={"_id": ObjectId(), "role": "super_admin"},
        )

    assert exc_info.value.status_code == 409
    assert len(projects.documents) == 1
    assert project_members.documents == []


def test_create_project_handles_unique_index_race(
    monkeypatch, fake_collection, project_members
):
    class DuplicateOnInsertCollection(fake_collection.__class__):
        def insert_one(self, document):
            raise DuplicateKeyError("duplicate project key")

    monkeypatch.setattr(
        project,
        "projects_collection",
        DuplicateOnInsertCollection(),
    )

    with pytest.raises(HTTPException) as exc_info:
        project.create_project(
            ProjectCreate(name="Concurrent", code="RACE"),
            current_user={"_id": ObjectId(), "role": "super_admin"},
        )

    assert exc_info.value.status_code == 409
    assert project_members.documents == []


def test_create_project_records_creator_as_initial_lead(
    monkeypatch, fake_collection, project_members
):
    projects = fake_collection.__class__()
    creator_id = ObjectId()
    monkeypatch.setattr(project, "projects_collection", projects)

    response = project.create_project(
        ProjectCreate(name="Support", code="SUP"),
        current_user={"_id": creator_id, "role": "super_admin"},
    )

    assert response["message"] == "Project created"
    assert projects.documents[0]["created_by"] == str(creator_id)
    assert "assigned_agent" not in projects.documents[0]
    assert "ticket_counter" not in projects.documents[0]
    assert project_members.documents[0] == {
        "_id": project_members.documents[0]["_id"],
        "project_id": projects.documents[0]["_id"],
        "user_id": creator_id,
        "role": "lead",
    }


def test_project_lead_and_super_admin_can_update_project(
    monkeypatch, fake_collection, grant_project_role
):
    project_id = ObjectId()
    lead_id = ObjectId()
    member_id = ObjectId()
    projects = fake_collection.__class__(
        [{"_id": project_id, "name": "Old", "code": "OLD"}]
    )
    monkeypatch.setattr(project, "projects_collection", projects)
    grant_project_role(project_id, lead_id, "lead")
    grant_project_role(project_id, member_id, "member")

    project.update_project(
        ProjectUpdate(name="Lead edit"),
        str(project_id),
        current_user={"_id": lead_id, "role": "user"},
    )
    assert projects.documents[0]["name"] == "Lead edit"

    project.update_project(
        ProjectUpdate(name="Admin edit"),
        str(project_id),
        current_user={"_id": ObjectId(), "role": "super_admin"},
    )
    assert projects.documents[0]["name"] == "Admin edit"

    with pytest.raises(HTTPException) as exc_info:
        project.update_project(
            ProjectUpdate(name="Member edit"),
            str(project_id),
            current_user={"_id": member_id, "role": "user"},
        )
    assert exc_info.value.status_code == 403


def test_update_project_rejects_duplicate_key(
    monkeypatch, fake_collection, grant_project_role
):
    project_id = ObjectId()
    other_project_id = ObjectId()
    lead_id = ObjectId()
    projects = fake_collection.__class__(
        [
            {"_id": project_id, "name": "One", "code": "ONE"},
            {"_id": other_project_id, "name": "Two", "code": "TWO"},
        ]
    )
    monkeypatch.setattr(project, "projects_collection", projects)
    grant_project_role(project_id, lead_id, "lead")

    with pytest.raises(HTTPException) as exc_info:
        project.update_project(
            ProjectUpdate(code="TWO"),
            str(project_id),
            current_user={"_id": lead_id, "role": "user"},
        )

    assert exc_info.value.status_code == 409
    assert projects.documents[0]["code"] == "ONE"


def test_update_project_handles_unique_index_race(
    monkeypatch, fake_collection, grant_project_role
):
    project_id = ObjectId()
    lead_id = ObjectId()

    class DuplicateOnUpdateCollection(fake_collection.__class__):
        def update_one(self, query, update):
            raise DuplicateKeyError("duplicate project key")

    projects = DuplicateOnUpdateCollection(
        [{"_id": project_id, "name": "One", "code": "ONE"}]
    )
    monkeypatch.setattr(project, "projects_collection", projects)
    grant_project_role(project_id, lead_id, "lead")

    with pytest.raises(HTTPException) as exc_info:
        project.update_project(
            ProjectUpdate(code="RACE"),
            str(project_id),
            current_user={"_id": lead_id, "role": "user"},
        )

    assert exc_info.value.status_code == 409
    assert projects.documents[0]["code"] == "ONE"


def test_project_lead_and_super_admin_can_add_members(
    monkeypatch,
    fake_collection,
    project_members,
    project_activities,
    grant_project_role,
):
    project_id = ObjectId()
    lead_id = ObjectId()
    member_id = ObjectId()
    admin_added_id = ObjectId()
    projects = fake_collection.__class__([{"_id": project_id, "code": "MEM"}])
    users = fake_collection.__class__(
        [
            {"_id": member_id, "name": "Member"},
            {"_id": admin_added_id, "name": "Reporter"},
        ]
    )
    monkeypatch.setattr(project, "projects_collection", projects)
    monkeypatch.setattr(project, "user_collection", users)
    grant_project_role(project_id, lead_id, "lead")

    project.add_project_member(
        str(project_id),
        ProjectMemberCreate(user_id=str(member_id), role="member"),
        current_user={"_id": lead_id, "role": "user"},
    )
    project.add_project_member(
        str(project_id),
        ProjectMemberCreate(user_id=str(admin_added_id), role="reporter"),
        current_user={"_id": ObjectId(), "role": "super_admin"},
    )

    roles_by_user = {
        document["user_id"]: document["role"] for document in project_members.documents
    }
    assert roles_by_user[member_id] == "member"
    assert roles_by_user[admin_added_id] == "reporter"
    assert [activity["action"] for activity in project_activities.documents] == [
        "project_member_added",
        "project_member_added",
    ]


def test_project_response_hides_retired_fields(
    monkeypatch, fake_collection, grant_project_role
):
    project_id = ObjectId()
    user_id = ObjectId()
    projects = fake_collection.__class__(
        [
            {
                "_id": project_id,
                "name": "Legacy",
                "code": "LEG",
                "assigned_agent": str(ObjectId()),
                "ticket_counter": 99,
                "workflow_transitions": [{"legacy": True}],
            }
        ]
    )
    monkeypatch.setattr(project, "projects_collection", projects)
    grant_project_role(project_id, user_id, "reporter")

    response = project.get_project(
        str(project_id),
        current_user={"_id": user_id, "role": "user"},
    )

    assert "assigned_agent" not in response["project"]
    assert "ticket_counter" not in response["project"]
    assert "workflow_transitions" not in response["project"]


def test_public_schema_exposes_project_routes():
    paths = app.openapi()["paths"]

    assert "/projects" in paths
    assert "/projects/{project_id}/members" in paths
    assert "/projects/{project_id}/statuses" in paths


def test_ticket_numbers_are_independent_per_project(
    monkeypatch, fake_collection, grant_project_role
):
    first_project_id = ObjectId()
    second_project_id = ObjectId()
    status_id = ObjectId()
    user_id = ObjectId()
    projects = fake_collection.__class__(
        [
            {
                "_id": first_project_id,
                "code": "ONE",
                "assigned_statuses": [str(status_id)],
            },
            {
                "_id": second_project_id,
                "code": "TWO",
                "assigned_statuses": [str(status_id)],
            },
        ]
    )
    statuses = fake_collection.__class__(
        [{"_id": status_id, "name": "To Do", "order": 1}]
    )
    tickets = fake_collection.__class__()
    counters = fake_collection.__class__()
    monkeypatch.setattr(ticket, "projects_collection", projects)
    monkeypatch.setattr(ticket, "statuses_collection", statuses)
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "counters_collection", counters)
    monkeypatch.setattr(ticket, "notifications_collection", fake_collection.__class__())
    monkeypatch.setattr(ticket, "log_activity", lambda **kwargs: None)
    grant_project_role(first_project_id, user_id, "reporter")
    grant_project_role(second_project_id, user_id, "reporter")

    def create(project_id, title):
        return ticket.create_ticket(
            TicketCreate(
                project_id=str(project_id),
                title=title,
                description="Description",
                category=Category.SUPPORT,
                priority=Priority.LOW,
            ),
            current_user={"_id": user_id, "role": "user"},
        )

    first = create(first_project_id, "First")
    second = create(first_project_id, "Second")
    other = create(second_project_id, "Other")

    assert [first["ticket_code"], second["ticket_code"], other["ticket_code"]] == [
        "ONE-1",
        "ONE-2",
        "TWO-1",
    ]
    assert first["project_id"] == str(first_project_id)
    assert first["project_key"] == "ONE"
    assert {document["_id"] for document in counters.documents} == {
        f"ticket_counter:{first_project_id}",
        f"ticket_counter:{second_project_id}",
    }


def test_new_project_counter_continues_existing_ticket_numbers(
    monkeypatch, fake_collection
):
    project_id = ObjectId()
    tickets = fake_collection.__class__(
        [
            {
                "_id": ObjectId(),
                "project_id": str(project_id),
                "ticket_code": "MIG-17",
            }
        ]
    )
    counters = fake_collection.__class__()
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "counters_collection", counters)

    next_number = ticket._next_project_ticket_number({"_id": project_id, "code": "MIG"})

    assert next_number == 18
