from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from fastapi import HTTPException
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from app.api import sprint
from app.models.sprint import (
    SprintCreate,
    SprintStatus,
    SprintUpdate,
    StoryPointsUpdate,
    TicketPositionUpdate,
    TicketSprintUpdate,
    _comparable_datetime,
)


def _project(project_id, status_ids=()):
    return {
        "_id": project_id,
        "code": "SPR",
        "name": "Sprint Coverage",
        "assigned_statuses": [str(status_id) for status_id in status_ids],
    }


def _allow_project_access(monkeypatch, project_id, project=None):
    project = project or _project(project_id)
    monkeypatch.setattr(
        sprint,
        "_get_project_or_404",
        lambda _project_id: (project_id, project),
    )
    monkeypatch.setattr(sprint, "_require_project_access", lambda *_args: {})
    monkeypatch.setattr(sprint, "_require_project_lead", lambda *_args: {})
    monkeypatch.setattr(sprint, "_require_project_work", lambda *_args: {})
    return project


def _allow_sprint_lifecycle(monkeypatch, sprint_document, project=None):
    project_id = sprint_document["project_id"]
    project = _allow_project_access(monkeypatch, project_id, project)
    monkeypatch.setattr(
        sprint,
        "_get_sprint_or_404",
        lambda _sprint_id: (sprint_document["_id"], sprint_document),
    )
    return project


def test_sprint_model_validation_edge_cases():
    naive = datetime(2026, 7, 1, 9, 0)
    assert _comparable_datetime(naive).tzinfo is timezone.utc

    with pytest.raises(ValidationError, match="Sprint name cannot be blank"):
        SprintCreate(name=" ")

    assert SprintUpdate(name=None).name is None
    assert SprintUpdate(name="  Renamed  ").name == "Renamed"

    with pytest.raises(ValidationError, match="Sprint name cannot be blank"):
        SprintUpdate(name=" ")

    with pytest.raises(ValidationError, match="end_date must be later"):
        SprintUpdate(start_date=naive, end_date=naive - timedelta(minutes=1))


def test_sprint_helper_error_paths():
    naive = datetime(2026, 7, 1, 9, 0)
    assert sprint._as_aware_utc(naive).tzinfo is timezone.utc

    with pytest.raises(HTTPException) as date_error:
        sprint._validate_date_range(naive, naive)
    assert date_error.value.status_code == 400

    assert sprint._story_points({"story_points": True}) == 0
    assert sprint._is_done_ticket({"closed_date": naive}, {}) is True

    with pytest.raises(HTTPException) as order_error:
        sprint._move_ticket_in_order(
            [],
            ObjectId(),
            0,
            "board_position",
            {"_id": ObjectId()},
        )
    assert order_error.value.status_code == 404

    with pytest.raises(HTTPException) as immutable_error:
        sprint._ensure_sprint_is_mutable({"status": SprintStatus.COMPLETED.value})
    assert immutable_error.value.status_code == 409

    assert sprint._move_tickets_to_backlog([], ObjectId(), {"_id": ObjectId()}) == []


@pytest.mark.parametrize(
    ("collection_name", "lookup", "detail"),
    [
        ("projects_collection", sprint._get_project_or_404, "Project not found"),
        ("sprints_collection", sprint._get_sprint_or_404, "Sprint not found"),
        ("ticket_collection", sprint._get_ticket_or_404, "Ticket not found"),
    ],
)
def test_sprint_lookup_helpers_return_404(
    monkeypatch,
    fake_collection,
    collection_name,
    lookup,
    detail,
):
    monkeypatch.setattr(sprint, collection_name, fake_collection)

    with pytest.raises(HTTPException) as exc_info:
        lookup(str(ObjectId()))

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == detail


def test_create_sprint_duplicate_precheck_and_insert_race(
    monkeypatch,
    fake_collection,
):
    project_id = ObjectId()
    user = {"_id": ObjectId(), "role": "agent"}
    _allow_project_access(monkeypatch, project_id)
    existing = {
        "_id": ObjectId(),
        "project_id": project_id,
        "name": "Sprint One",
        "name_key": "sprint one",
        "status": SprintStatus.PLANNED.value,
    }
    monkeypatch.setattr(
        sprint,
        "sprints_collection",
        fake_collection.__class__([existing]),
    )

    with pytest.raises(HTTPException) as duplicate_error:
        sprint.create_sprint(
            str(project_id),
            SprintCreate(name="Sprint One"),
            current_user=user,
        )
    assert duplicate_error.value.status_code == 409

    class InsertRaceCollection:
        @staticmethod
        def find_one(_query):
            return None

        @staticmethod
        def insert_one(_document):
            raise DuplicateKeyError("duplicate sprint")

    monkeypatch.setattr(sprint, "sprints_collection", InsertRaceCollection())

    with pytest.raises(HTTPException) as race_error:
        sprint.create_sprint(
            str(project_id),
            SprintCreate(name="Sprint Two"),
            current_user=user,
        )
    assert race_error.value.status_code == 409


def test_list_filter_and_get_sprint_details(monkeypatch, fake_collection):
    project_id = ObjectId()
    sprint_id = ObjectId()
    user = {"_id": ObjectId(), "role": "agent"}
    project = _allow_project_access(monkeypatch, project_id)
    sprint_document = {
        "_id": sprint_id,
        "project_id": project_id,
        "name": "Filtered",
        "name_key": "filtered",
        "status": SprintStatus.PLANNED.value,
        "created_at": datetime.now(timezone.utc),
    }
    monkeypatch.setattr(
        sprint,
        "sprints_collection",
        fake_collection.__class__([sprint_document]),
    )
    monkeypatch.setattr(sprint, "ticket_collection", fake_collection.__class__())

    listed = sprint.list_sprints(
        str(project_id),
        sprint_status=SprintStatus.PLANNED,
        current_user=user,
    )
    assert [item["name"] for item in listed] == ["Filtered"]

    ticket = {
        "_id": ObjectId(),
        "project_id": str(project_id),
        "ticket_code": "SPR-1",
        "sprint_id": str(sprint_id),
        "current_status_name": "Open",
        "story_points": 3,
        "board_position": 0,
    }
    monkeypatch.setattr(
        sprint,
        "_get_sprint_or_404",
        lambda _sprint_id: (sprint_id, sprint_document),
    )
    monkeypatch.setattr(
        sprint,
        "ticket_collection",
        fake_collection.__class__([ticket]),
    )

    result = sprint.get_sprint(str(sprint_id), current_user=user)
    assert result["current_scope"]["committed_story_points"] == 3
    assert result["tickets"][0]["ticket_code"] == "SPR-1"
    assert result["project_id"] == str(project["_id"])


def test_update_sprint_success_and_validation_paths(monkeypatch, fake_collection):
    project_id = ObjectId()
    sprint_id = ObjectId()
    user = {"_id": ObjectId(), "role": "agent"}
    start = datetime(2026, 7, 1, 9, 0)
    end = start + timedelta(days=7)
    sprint_document = {
        "_id": sprint_id,
        "project_id": project_id,
        "name": "Original",
        "name_key": "original",
        "status": SprintStatus.PLANNED.value,
    }
    _allow_sprint_lifecycle(monkeypatch, sprint_document)
    collection = fake_collection.__class__([sprint_document])
    monkeypatch.setattr(sprint, "sprints_collection", collection)

    updated = sprint.update_sprint(
        str(sprint_id),
        SprintUpdate(name=" Renamed ", start_date=start, end_date=end),
        current_user=user,
    )
    assert updated["name"] == "Renamed"
    assert "name_key" not in updated
    assert collection.documents[0]["name_key"] == "renamed"
    assert updated["start_date"].tzinfo is timezone.utc

    with pytest.raises(HTTPException) as empty_error:
        sprint.update_sprint(
            str(sprint_id),
            SprintUpdate(),
            current_user=user,
        )
    assert empty_error.value.status_code == 400


def test_update_sprint_duplicate_name_and_update_race(monkeypatch, fake_collection):
    project_id = ObjectId()
    sprint_id = ObjectId()
    user = {"_id": ObjectId(), "role": "agent"}
    sprint_document = {
        "_id": sprint_id,
        "project_id": project_id,
        "name": "Original",
        "name_key": "original",
        "status": SprintStatus.PLANNED.value,
    }
    _allow_sprint_lifecycle(monkeypatch, sprint_document)
    duplicate = {
        "_id": ObjectId(),
        "project_id": project_id,
        "name": "Taken",
        "name_key": "taken",
        "status": SprintStatus.PLANNED.value,
    }
    monkeypatch.setattr(
        sprint,
        "sprints_collection",
        fake_collection.__class__([duplicate]),
    )

    with pytest.raises(HTTPException) as duplicate_error:
        sprint.update_sprint(
            str(sprint_id),
            SprintUpdate(name="Taken"),
            current_user=user,
        )
    assert duplicate_error.value.status_code == 409

    class UpdateRaceCollection:
        @staticmethod
        def update_one(_query, _update):
            raise DuplicateKeyError("duplicate sprint")

    monkeypatch.setattr(sprint, "sprints_collection", UpdateRaceCollection())

    with pytest.raises(HTTPException) as race_error:
        sprint.update_sprint(
            str(sprint_id),
            SprintUpdate(goal="Updated goal"),
            current_user=user,
        )
    assert race_error.value.status_code == 409


def test_start_sprint_rejects_invalid_state_and_duplicate_key(
    monkeypatch,
    fake_collection,
):
    project_id = ObjectId()
    sprint_id = ObjectId()
    user = {"_id": ObjectId(), "role": "agent"}
    sprint_document = {
        "_id": sprint_id,
        "project_id": project_id,
        "name": "Sprint",
        "status": SprintStatus.ACTIVE.value,
    }
    project = _allow_sprint_lifecycle(monkeypatch, sprint_document)

    with pytest.raises(HTTPException) as state_error:
        sprint.start_sprint(str(sprint_id), current_user=user)
    assert state_error.value.status_code == 409

    sprint_document["status"] = SprintStatus.PLANNED.value
    monkeypatch.setattr(sprint, "ticket_collection", fake_collection.__class__())

    class StartRaceCollection:
        @staticmethod
        def find_one(_query):
            return None

        @staticmethod
        def update_one(_query, _update):
            raise DuplicateKeyError("active sprint race")

    monkeypatch.setattr(sprint, "sprints_collection", StartRaceCollection())
    assert project["assigned_statuses"] == []

    with pytest.raises(HTTPException) as race_error:
        sprint.start_sprint(str(sprint_id), current_user=user)
    assert race_error.value.status_code == 409


def test_complete_and_delete_sprint_state_paths(monkeypatch, fake_collection):
    project_id = ObjectId()
    sprint_id = ObjectId()
    user = {"_id": ObjectId(), "role": "agent"}
    sprint_document = {
        "_id": sprint_id,
        "project_id": project_id,
        "name": "Sprint",
        "status": SprintStatus.PLANNED.value,
    }
    _allow_sprint_lifecycle(monkeypatch, sprint_document)
    monkeypatch.setattr(sprint, "ticket_collection", fake_collection.__class__())
    collection = fake_collection.__class__([sprint_document])
    monkeypatch.setattr(sprint, "sprints_collection", collection)

    with pytest.raises(HTTPException) as complete_error:
        sprint.complete_sprint(str(sprint_id), current_user=user)
    assert complete_error.value.status_code == 409

    deleted = sprint.delete_sprint(str(sprint_id), current_user=user)
    assert deleted == {"message": "Sprint deleted", "moved_to_backlog": []}
    assert collection.documents == []

    sprint_document["status"] = SprintStatus.ACTIVE.value
    with pytest.raises(HTTPException) as delete_error:
        sprint.delete_sprint(str(sprint_id), current_user=user)
    assert delete_error.value.status_code == 409


def test_get_backlog_and_board_argument_paths(monkeypatch, fake_collection):
    project_id = ObjectId()
    sprint_id = ObjectId()
    other_project_id = ObjectId()
    user = {"_id": ObjectId(), "role": "agent"}
    project = _allow_project_access(monkeypatch, project_id)
    backlog_ticket = {
        "_id": ObjectId(),
        "project_id": str(project_id),
        "ticket_code": "SPR-1",
        "sprint_id": None,
        "story_points": 5,
        "backlog_position": 0,
    }
    monkeypatch.setattr(
        sprint,
        "ticket_collection",
        fake_collection.__class__([backlog_ticket]),
    )
    monkeypatch.setattr(sprint, "statuses_collection", fake_collection.__class__())

    backlog = sprint.get_backlog(str(project_id), current_user=user)
    assert backlog["total_tickets"] == 1
    assert backlog["total_story_points"] == 5

    with pytest.raises(HTTPException) as arguments_error:
        sprint.get_sprint_board(
            str(project_id),
            sprint_id=str(sprint_id),
            backlog=True,
            current_user=user,
        )
    assert arguments_error.value.status_code == 400

    selected_sprint = {
        "_id": sprint_id,
        "project_id": other_project_id,
        "name": "Other Project",
        "status": SprintStatus.PLANNED.value,
    }
    monkeypatch.setattr(
        sprint,
        "_get_sprint_or_404",
        lambda _sprint_id: (sprint_id, selected_sprint),
    )
    with pytest.raises(HTTPException) as project_error:
        sprint.get_sprint_board(
            str(project_id),
            sprint_id=str(sprint_id),
            backlog=False,
            current_user=user,
        )
    assert project_error.value.status_code == 400

    selected_sprint["project_id"] = project_id
    board = sprint.get_sprint_board(
        str(project_id),
        sprint_id=str(sprint_id),
        backlog=False,
        current_user=user,
    )
    assert board["scope"] == "sprint"
    assert board["sprint"]["_id"] == str(sprint_id)
    assert project["assigned_statuses"] == []


def test_ticket_sprint_cross_project_and_noop_paths(monkeypatch):
    project_id = ObjectId()
    sprint_id = ObjectId()
    user = {"_id": ObjectId(), "role": "agent"}
    ticket = {
        "_id": ObjectId(),
        "project_id": str(project_id),
        "ticket_code": "SPR-1",
        "current_status_id": str(ObjectId()),
        "sprint_id": None,
    }
    _allow_project_access(monkeypatch, project_id)
    monkeypatch.setattr(
        sprint,
        "_get_ticket_or_404",
        lambda _ticket_id: (ticket["_id"], ticket),
    )
    target = {
        "_id": sprint_id,
        "project_id": ObjectId(),
        "status": SprintStatus.PLANNED.value,
    }
    monkeypatch.setattr(
        sprint,
        "_get_sprint_or_404",
        lambda _sprint_id: (sprint_id, target),
    )

    with pytest.raises(HTTPException) as project_error:
        sprint.set_ticket_sprint(
            str(ticket["_id"]),
            TicketSprintUpdate(sprint_id=str(sprint_id)),
            current_user=user,
        )
    assert project_error.value.status_code == 400

    ticket["sprint_id"] = str(sprint_id)
    target["project_id"] = project_id
    unchanged = sprint.set_ticket_sprint(
        str(ticket["_id"]),
        TicketSprintUpdate(sprint_id=str(sprint_id)),
        current_user=user,
    )
    assert unchanged["sprint_id"] == str(sprint_id)


def test_ticket_estimate_and_backlog_position_scope_guards(
    monkeypatch,
    fake_collection,
):
    project_id = ObjectId()
    sprint_id = ObjectId()
    user = {"_id": ObjectId(), "role": "agent"}
    ticket = {
        "_id": ObjectId(),
        "project_id": str(project_id),
        "ticket_code": "SPR-1",
        "current_status_id": str(ObjectId()),
        "sprint_id": str(sprint_id),
        "story_points": None,
    }
    _allow_project_access(monkeypatch, project_id)
    monkeypatch.setattr(
        sprint,
        "_get_ticket_or_404",
        lambda _ticket_id: (ticket["_id"], ticket),
    )
    monkeypatch.setattr(
        sprint,
        "_get_sprint_or_404",
        lambda _sprint_id: (
            sprint_id,
            {
                "_id": sprint_id,
                "project_id": project_id,
                "status": SprintStatus.PLANNED.value,
            },
        ),
    )
    tickets = fake_collection.__class__([ticket])
    monkeypatch.setattr(sprint, "ticket_collection", tickets)
    monkeypatch.setattr(sprint, "log_activity", lambda **_kwargs: None)

    estimated = sprint.set_story_points(
        str(ticket["_id"]),
        StoryPointsUpdate(story_points=8),
        current_user=user,
    )
    assert estimated["story_points"] == 8

    with pytest.raises(HTTPException) as position_error:
        sprint.reorder_backlog_ticket(
            str(ticket["_id"]),
            TicketPositionUpdate(position=0),
            current_user=user,
        )
    assert position_error.value.status_code == 409
