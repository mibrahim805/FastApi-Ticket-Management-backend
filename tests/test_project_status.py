import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.api import project, status
from app.models.project import ProjectStatusAssign, StatusMigration
from app.models.status import StatusCreate, StatusUpdate


def test_assign_statuses_to_project_orders_statuses(
    monkeypatch,
    fake_collection,
    transitions,
    grant_project_role,
):
    project_id = ObjectId()
    lead_id = ObjectId()
    first_id = ObjectId()
    second_id = ObjectId()

    projects = fake_collection.__class__([{"_id": project_id}])
    statuses = fake_collection.__class__(
        [
            {"_id": second_id, "name": "Done", "order": 2},
            {"_id": first_id, "name": "Open", "order": 1},
        ]
    )

    monkeypatch.setattr(project, "projects_collection", projects)
    monkeypatch.setattr(project, "statuses_collection", statuses)
    transitions.insert_one(
        {
            "_id": ObjectId(),
            "project_id": project_id,
            "name": "Old workflow",
        }
    )
    grant_project_role(project_id, lead_id)

    response = project.assign_statuses_to_project(
        str(project_id),
        ProjectStatusAssign(status_ids=[str(second_id), str(first_id)]),
        current_user={"_id": lead_id, "role": "super_admin"},
    )

    assert response["assigned_statuses"][0]["name"] == "Open"
    assert projects.documents[0]["assigned_statuses"] == [str(first_id), str(second_id)]
    assert projects.documents[0]["default_status_id"] == str(first_id)
    assert transitions.documents == []


def test_assign_statuses_requires_and_applies_ticket_status_migration(
    monkeypatch, fake_collection, grant_project_role
):
    project_id = ObjectId()
    lead_id = ObjectId()
    removed_id = ObjectId()
    target_id = ObjectId()
    keep_id = ObjectId()
    projects = fake_collection.__class__(
        [
            {
                "_id": project_id,
                "assigned_statuses": [
                    str(removed_id),
                    str(target_id),
                    str(keep_id),
                ],
            }
        ]
    )
    statuses = fake_collection.__class__(
        [
            {"_id": removed_id, "name": "Old", "order": 1},
            {"_id": target_id, "name": "New", "order": 2},
            {"_id": keep_id, "name": "Keep", "order": 3},
        ]
    )
    tickets = fake_collection.__class__(
        [
            {
                "_id": ObjectId(),
                "project_id": str(project_id),
                "current_status_id": str(removed_id),
                "current_status_name": "Old",
            }
        ]
    )
    monkeypatch.setattr(project, "projects_collection", projects)
    monkeypatch.setattr(project, "statuses_collection", statuses)
    monkeypatch.setattr(project, "ticket_collection", tickets)
    grant_project_role(project_id, lead_id)

    with pytest.raises(HTTPException) as exc_info:
        project.assign_statuses_to_project(
            str(project_id),
            ProjectStatusAssign(status_ids=[str(target_id), str(keep_id)]),
            current_user={"_id": lead_id, "role": "super_admin"},
        )
    assert exc_info.value.status_code == 400

    project.assign_statuses_to_project(
        str(project_id),
        ProjectStatusAssign(
            status_ids=[str(target_id), str(keep_id)],
            status_migrations=[
                StatusMigration(
                    remove_status_id=str(removed_id),
                    move_existing_tickets_to=str(target_id),
                )
            ],
        ),
        current_user={"_id": lead_id, "role": "super_admin"},
    )
    assert tickets.documents[0]["current_status_id"] == str(target_id)
    assert tickets.documents[0]["current_status_name"] == "New"


def test_renaming_status_updates_denormalized_ticket_names(
    monkeypatch, fake_collection
):
    status_id = ObjectId()
    statuses = fake_collection.__class__([{"_id": status_id, "name": "Old"}])
    tickets = fake_collection.__class__(
        [
            {
                "_id": ObjectId(),
                "current_status_id": str(status_id),
                "current_status_name": "Old",
            }
        ]
    )
    monkeypatch.setattr(status, "statuses_collection", statuses)
    monkeypatch.setattr(status, "ticket_collection", tickets)

    status.update_status(
        str(status_id),
        StatusUpdate(name="Renamed"),
        current_user={"role": "super_admin"},
    )

    assert tickets.documents[0]["current_status_name"] == "Renamed"


def test_assign_statuses_to_project_rejects_non_admin(
    monkeypatch, fake_collection, grant_project_role
):
    project_id = ObjectId()
    member_id = ObjectId()
    projects = fake_collection.__class__([{"_id": project_id}])
    monkeypatch.setattr(project, "projects_collection", projects)
    monkeypatch.setattr(project, "statuses_collection", fake_collection.__class__())
    grant_project_role(project_id, member_id, "member")

    with pytest.raises(HTTPException) as exc_info:
        project.assign_statuses_to_project(
            str(project_id),
            ProjectStatusAssign(status_ids=[str(ObjectId()), str(ObjectId())]),
            current_user={"_id": member_id, "role": "agent"},
        )

    assert exc_info.value.status_code == 403


def test_assign_statuses_to_project_rejects_duplicate_ids(
    monkeypatch, fake_collection, grant_project_role
):
    project_id = ObjectId()
    lead_id = ObjectId()
    status_id = ObjectId()
    projects = fake_collection.__class__([{"_id": project_id}])
    monkeypatch.setattr(project, "projects_collection", projects)
    monkeypatch.setattr(
        project,
        "statuses_collection",
        fake_collection.__class__([{"_id": status_id, "name": "Open", "order": 1}]),
    )
    grant_project_role(project_id, lead_id)

    with pytest.raises(HTTPException) as exc_info:
        project.assign_statuses_to_project(
            str(project_id),
            ProjectStatusAssign(status_ids=[str(status_id), str(status_id)]),
            current_user={"_id": lead_id, "role": "super_admin"},
        )

    assert exc_info.value.status_code == 400


def test_create_status_rejects_duplicate_name(monkeypatch, fake_collection):
    statuses = fake_collection.__class__([{"name": "Open"}])
    monkeypatch.setattr(status, "statuses_collection", statuses)

    with pytest.raises(HTTPException) as exc_info:
        status.create_status(
            StatusCreate(name="Open", order=1),
            current_user={"_id": ObjectId(), "role": "super_admin"},
        )

    assert exc_info.value.status_code == 400


def test_create_status_rejects_non_admin(monkeypatch, fake_collection):
    monkeypatch.setattr(status, "statuses_collection", fake_collection)

    with pytest.raises(HTTPException) as exc_info:
        status.create_status(
            StatusCreate(name="Open", order=1),
            current_user={"_id": ObjectId(), "role": "agent"},
        )

    assert exc_info.value.status_code == 403


def test_list_statuses_rejects_non_super_admin(monkeypatch, fake_collection):
    monkeypatch.setattr(status, "statuses_collection", fake_collection)

    with pytest.raises(HTTPException) as exc_info:
        status.list_statuses(
            current_user={"_id": ObjectId(), "role": "agent"},
        )

    assert exc_info.value.status_code == 403


def test_create_default_workflow_statuses_creates_missing_statuses(
    monkeypatch, fake_collection
):
    statuses = fake_collection.__class__()
    monkeypatch.setattr(status, "statuses_collection", statuses)

    response = status.create_default_workflow_statuses(
        current_user={"_id": ObjectId(), "role": "super_admin"}
    )

    created_names = {item["name"] for item in response["created_statuses"]}
    assert response["created_count"] == 11
    assert "QA Failed" in created_names
    assert "Ready for Deployment" in created_names
    assert response["workflow_statuses"][0]["name"] == "To Do"


def test_create_default_workflow_statuses_is_idempotent(monkeypatch, fake_collection):
    statuses = fake_collection.__class__()
    monkeypatch.setattr(status, "statuses_collection", statuses)

    status.create_default_workflow_statuses(
        current_user={"_id": ObjectId(), "role": "super_admin"}
    )
    response = status.create_default_workflow_statuses(
        current_user={"_id": ObjectId(), "role": "super_admin"}
    )

    assert response["created_count"] == 0
    assert len(statuses.documents) == 11


def test_update_status_rejects_empty_payload(monkeypatch, fake_collection):
    status_id = ObjectId()
    statuses = fake_collection.__class__([{"_id": status_id, "name": "Open"}])
    monkeypatch.setattr(status, "statuses_collection", statuses)

    with pytest.raises(HTTPException) as exc_info:
        status.update_status(
            str(status_id),
            StatusUpdate(),
            current_user={"_id": ObjectId(), "role": "super_admin"},
        )

    assert exc_info.value.status_code == 400


def test_update_status_rejects_missing_status(monkeypatch, fake_collection):
    monkeypatch.setattr(status, "statuses_collection", fake_collection)

    with pytest.raises(HTTPException) as exc_info:
        status.update_status(
            str(ObjectId()),
            StatusUpdate(name="New"),
            current_user={"_id": ObjectId(), "role": "super_admin"},
        )

    assert exc_info.value.status_code == 404
