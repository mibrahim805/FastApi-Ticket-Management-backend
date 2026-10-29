from datetime import datetime, timezone

import pytest
from bson import ObjectId
from fastapi import HTTPException
from pydantic import ValidationError

from app.api import filters, ticket
from app.main import app
from app.models.saved_filter import SavedFilterCreate


def _ticket_search_arguments(**overrides):
    arguments = {
        "page": 1,
        "page_size": 10,
        "status": None,
        "priority": None,
        "category": None,
        "issue_type": None,
        "labels": None,
        "epic_id": None,
        "assigned_agent": None,
        "search": None,
        "overdue_only": None,
        "sort_by": "created_at",
        "sort_order": "desc",
    }
    arguments.update(overrides)
    return arguments


def test_board_search_matches_description_and_filters_by_epic(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    epic_id = ObjectId()
    other_epic_id = ObjectId()
    user_id = ObjectId()
    monkeypatch.setattr(
        ticket,
        "projects_collection",
        fake_collection.__class__([{"_id": project_id, "code": "SEARCH"}]),
    )
    monkeypatch.setattr(
        ticket,
        "ticket_collection",
        fake_collection.__class__(
            [
                {
                    "_id": ObjectId(),
                    "project_id": str(project_id),
                    "ticket_code": "SEARCH-1",
                    "title": "API failure",
                    "description": "Database connection timeout",
                    "epic_id": str(epic_id),
                    "created_date": datetime(2026, 7, 1, tzinfo=timezone.utc),
                },
                {
                    "_id": ObjectId(),
                    "project_id": str(project_id),
                    "ticket_code": "SEARCH-2",
                    "title": "Frontend cleanup",
                    "description": "Update the settings screen",
                    "epic_id": str(other_epic_id),
                    "created_date": datetime(2026, 7, 2, tzinfo=timezone.utc),
                },
            ]
        ),
    )
    grant_project_role(project_id, user_id, "member")
    current_user = {"_id": user_id, "role": "agent"}

    description_result = ticket.get_tickets(
        "SEARCH",
        **_ticket_search_arguments(search="connection timeout"),
        current_user=current_user,
    )
    epic_result = ticket.get_tickets(
        "SEARCH",
        **_ticket_search_arguments(epic_id=str(other_epic_id)),
        current_user=current_user,
    )

    assert [item["ticket_code"] for item in description_result["data"]] == ["SEARCH-1"]
    assert [item["ticket_code"] for item in epic_result["data"]] == ["SEARCH-2"]
    assert epic_result["filters_applied"]["epic_id"] == str(other_epic_id)


def test_ticket_search_filters_by_current_status_id_for_name_or_id(
    monkeypatch, fake_collection
):
    status_id = ObjectId()
    monkeypatch.setattr(
        ticket,
        "statuses_collection",
        fake_collection.__class__([{"_id": status_id, "name": "To Do"}]),
    )

    by_name = ticket._build_ticket_list_query(
        project_ids=["project"],
        status="to do",
        priority=None,
        category=None,
        issue_type=None,
        labels=None,
        epic_id=None,
        assigned_agent=None,
        search=None,
        overdue_only=None,
    )
    by_id = ticket._build_ticket_list_query(
        project_ids=["project"],
        status=str(status_id),
        priority=None,
        category=None,
        issue_type=None,
        labels=None,
        epic_id=None,
        assigned_agent=None,
        search=None,
        overdue_only=None,
    )

    assert by_name["current_status_id"] == str(status_id)
    assert by_id["current_status_id"] == str(status_id)
    assert "status" not in by_name


def test_global_search_only_returns_tickets_from_accessible_projects(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    first_project_id = ObjectId()
    second_project_id = ObjectId()
    inaccessible_project_id = ObjectId()
    user_id = ObjectId()
    grant_project_role(first_project_id, user_id, "member")
    grant_project_role(second_project_id, user_id, "reporter")
    monkeypatch.setattr(
        ticket,
        "ticket_collection",
        fake_collection.__class__(
            [
                {
                    "_id": ObjectId(),
                    "project_id": str(first_project_id),
                    "ticket_code": "ONE-1",
                    "title": "Shared search phrase",
                    "description": "First project",
                    "created_date": datetime(2026, 7, 1, tzinfo=timezone.utc),
                },
                {
                    "_id": ObjectId(),
                    "project_id": str(second_project_id),
                    "ticket_code": "TWO-1",
                    "title": "Second project",
                    "description": "Contains the shared search phrase",
                    "created_date": datetime(2026, 7, 2, tzinfo=timezone.utc),
                },
                {
                    "_id": ObjectId(),
                    "project_id": str(inaccessible_project_id),
                    "ticket_code": "SECRET-1",
                    "title": "Shared search phrase",
                    "description": "Must remain inaccessible",
                    "created_date": datetime(2026, 7, 3, tzinfo=timezone.utc),
                },
            ]
        ),
    )

    response = ticket.search_tickets(
        **_ticket_search_arguments(search="shared search phrase"),
        current_user={"_id": user_id, "role": "agent"},
    )

    assert {item["ticket_code"] for item in response["data"]} == {
        "ONE-1",
        "TWO-1",
    }
    assert response["pagination"]["total"] == 2
    assert response["scope"] == "all_accessible_projects"


def test_global_search_allows_super_admin_to_search_every_project(
    monkeypatch,
    fake_collection,
):
    first_project_id = ObjectId()
    second_project_id = ObjectId()
    monkeypatch.setattr(
        ticket,
        "projects_collection",
        fake_collection.__class__(
            [{"_id": first_project_id}, {"_id": second_project_id}]
        ),
    )
    monkeypatch.setattr(
        ticket,
        "ticket_collection",
        fake_collection.__class__(
            [
                {
                    "_id": ObjectId(),
                    "project_id": str(first_project_id),
                    "ticket_code": "ONE-1",
                    "title": "First",
                    "description": "Release blocker",
                    "created_date": datetime(2026, 7, 1, tzinfo=timezone.utc),
                },
                {
                    "_id": ObjectId(),
                    "project_id": str(second_project_id),
                    "ticket_code": "TWO-1",
                    "title": "Second",
                    "description": "Release blocker",
                    "created_date": datetime(2026, 7, 2, tzinfo=timezone.utc),
                },
            ]
        ),
    )

    response = ticket.search_tickets(
        **_ticket_search_arguments(search="release blocker"),
        current_user={"_id": ObjectId(), "role": "super_admin"},
    )

    assert response["pagination"]["total"] == 2


def test_user_can_create_list_and_delete_own_saved_filter(saved_filters):
    user_id = ObjectId()
    other_user_id = ObjectId()
    saved_filters.insert_one(
        {
            "user_id": str(other_user_id),
            "name": "Other user's filter",
            "name_key": "other user's filter",
            "query_params": {"priority": "low"},
            "created_at": datetime(2026, 7, 1, tzinfo=timezone.utc),
        }
    )
    current_user = {"_id": user_id, "role": "agent"}

    created = filters.create_saved_filter(
        SavedFilterCreate(
            name="  Urgent backend  ",
            query_params={
                "priority": "urgent",
                "labels": ["backend"],
                "scope": "global",
            },
        ),
        current_user=current_user,
    )
    listed = filters.list_saved_filters(current_user=current_user)

    assert created["name"] == "Urgent backend"
    assert created["query_params"]["priority"] == "urgent"
    assert [item["filter_id"] for item in listed["filters"]] == [created["filter_id"]]

    response = filters.delete_saved_filter(
        created["filter_id"],
        current_user=current_user,
    )

    assert response == {"message": "Saved filter deleted successfully"}
    assert filters.list_saved_filters(current_user=current_user)["filters"] == []


def test_saved_filter_names_are_unique_per_user(saved_filters):
    user_id = ObjectId()
    current_user = {"_id": user_id, "role": "agent"}
    filters.create_saved_filter(
        SavedFilterCreate(name="My Work", query_params={}),
        current_user=current_user,
    )

    with pytest.raises(HTTPException) as exc_info:
        filters.create_saved_filter(
            SavedFilterCreate(name="my work", query_params={}),
            current_user=current_user,
        )

    assert exc_info.value.status_code == 409


def test_user_cannot_delete_another_users_saved_filter(saved_filters):
    filter_id = ObjectId()
    saved_filters.insert_one(
        {
            "_id": filter_id,
            "user_id": str(ObjectId()),
            "name": "Private",
            "name_key": "private",
            "query_params": {},
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        filters.delete_saved_filter(
            str(filter_id),
            current_user={"_id": ObjectId(), "role": "agent"},
        )

    assert exc_info.value.status_code == 404
    assert len(saved_filters.documents) == 1


def test_saved_filter_rejects_blank_name():
    with pytest.raises(ValidationError):
        SavedFilterCreate(name="   ", query_params={})


def test_search_and_saved_filter_routes_are_exposed_in_openapi():
    paths = app.openapi()["paths"]
    ticket_parameters = paths["/tickets/{project_key}"]["get"]["parameters"]
    ticket_route_paths = [route.path for route in ticket.router.routes]

    assert "/tickets/search" in paths
    assert ticket_route_paths.index("/tickets/search") < ticket_route_paths.index(
        "/tickets/{project_key}"
    )
    assert {parameter["name"] for parameter in ticket_parameters} >= {
        "epic_id",
        "labels",
        "search",
    }
    assert set(paths["/filters"]) == {"get", "post"}
    assert set(paths["/filters/{filter_id}"]) == {"delete"}
