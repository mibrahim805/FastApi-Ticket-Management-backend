import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import HTTPException
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError
from starlette.requests import Request

from app.api import (
    comment,
    filters,
    notification_delivery,
    notifications,
    permissions,
    rate_limit,
    status,
    ticket,
)
from app.models.comment import CommentUpdate, CommentVisibility
from app.models.registration import UserRegister
from app.models.saved_filter import SavedFilterCreate
from app.models.ticket import (
    Category,
    IssueLinkCreate,
    IssueLinkType,
    IssueType,
    Priority,
    TicketCreate,
    TicketUpdate,
)


def _assert_http_error(call, status_code, detail):
    with pytest.raises(HTTPException) as exc_info:
        call()
    assert exc_info.value.status_code == status_code
    assert exc_info.value.detail == detail


def test_comment_lookup_visibility_serialization_and_email_fallback(
    monkeypatch,
    fake_collection,
):
    monkeypatch.setattr(comment, "ticket_collection", fake_collection.__class__())
    monkeypatch.setattr(comment, "comments_collection", fake_collection.__class__())

    _assert_http_error(
        lambda: comment._get_ticket_or_404(str(ObjectId())),
        404,
        "Ticket not found",
    )
    _assert_http_error(
        lambda: comment._get_comment_or_404(str(ObjectId())),
        404,
        "Comment not found",
    )
    _assert_http_error(
        lambda: comment._require_comment_visible(
            {"visibility": CommentVisibility.INTERNAL.value},
            {"role": "reporter"},
        ),
        404,
        "Comment not found",
    )

    parent_id = ObjectId()
    serialized = comment._serialize_comment({"_id": ObjectId(), "parent_id": parent_id})
    assert serialized["parent_id"] == str(parent_id)

    email_user = {
        "_id": ObjectId(),
        "email": "alice@example.com",
    }
    monkeypatch.setattr(
        comment,
        "user_collection",
        fake_collection.__class__([email_user]),
    )
    assert comment._find_user_by_handle("alice")["_id"] == email_user["_id"]


def test_comment_mention_resolution_error_and_skip_paths(monkeypatch):
    actor_id = ObjectId()
    ticket_doc = {"project_id": str(ObjectId())}

    monkeypatch.setattr(comment, "_find_user_by_handle", lambda _handle: None)
    _assert_http_error(
        lambda: comment._resolve_mentions(
            message="@missing",
            ticket=ticket_doc,
            visibility="public",
            actor_id=actor_id,
        ),
        400,
        "Mentioned user '@missing' was not found",
    )

    monkeypatch.setattr(
        comment,
        "_find_user_by_handle",
        lambda _handle: {"_id": actor_id, "is_active": True},
    )
    assert (
        comment._resolve_mentions(
            message="@self",
            ticket=ticket_doc,
            visibility="public",
            actor_id=actor_id,
        )
        == []
    )

    inactive_id = ObjectId()
    monkeypatch.setattr(
        comment,
        "_find_user_by_handle",
        lambda _handle: {"_id": inactive_id, "is_active": False},
    )
    _assert_http_error(
        lambda: comment._resolve_mentions(
            message="@inactive",
            ticket=ticket_doc,
            visibility="public",
            actor_id=actor_id,
        ),
        400,
        "Mentioned user '@inactive' is inactive",
    )


@pytest.mark.parametrize(
    ("parent", "ticket_id", "visibility", "detail"),
    [
        (
            {"ticket_id": "other", "visibility": "public"},
            "ticket",
            "public",
            "Parent comment belongs to a different ticket",
        ),
        (
            {"ticket_id": "ticket", "visibility": "internal"},
            "ticket",
            "public",
            "A reply must use the same visibility as its parent comment",
        ),
    ],
)
def test_validate_parent_comment_rejects_mismatches(
    monkeypatch,
    parent,
    ticket_id,
    visibility,
    detail,
):
    monkeypatch.setattr(
        comment,
        "_get_comment_or_404",
        lambda _comment_id: (ObjectId(), parent),
    )
    _assert_http_error(
        lambda: comment._validate_parent_comment(
            parent_id=str(ObjectId()),
            ticket_id=ticket_id,
            visibility=visibility,
            membership={"role": "member"},
        ),
        400,
        detail,
    )


def _patch_comment_update_access(monkeypatch, existing_comment, membership):
    monkeypatch.setattr(
        comment,
        "_get_comment_or_404",
        lambda _comment_id: (existing_comment["_id"], existing_comment),
    )
    monkeypatch.setattr(
        comment,
        "_get_ticket_or_404",
        lambda _ticket_id: (
            str(existing_comment["ticket_id"]),
            {"project_id": str(ObjectId())},
        ),
    )
    monkeypatch.setattr(
        comment,
        "require_ticket_participant",
        lambda *_args: membership,
    )


def test_update_comment_rejects_empty_and_reporter_internal_updates(monkeypatch):
    author_id = ObjectId()
    existing = {
        "_id": ObjectId(),
        "ticket_id": str(ObjectId()),
        "author_id": str(author_id),
        "message": "before",
        "visibility": "public",
        "parent_id": None,
    }
    current_user = {"_id": author_id, "role": "customer"}

    _patch_comment_update_access(monkeypatch, existing, {"role": "member"})
    _assert_http_error(
        lambda: comment.update_comment(
            str(existing["_id"]),
            CommentUpdate(),
            current_user=current_user,
        ),
        400,
        "No comment fields supplied",
    )

    _patch_comment_update_access(monkeypatch, existing, {"role": "reporter"})
    _assert_http_error(
        lambda: comment.update_comment(
            str(existing["_id"]),
            CommentUpdate(visibility=CommentVisibility.INTERNAL),
            current_user=current_user,
        ),
        403,
        "You do not have permission to create internal comments",
    )


def test_update_comment_validates_parent_and_blocks_visibility_with_replies(
    monkeypatch,
    fake_collection,
):
    author_id = ObjectId()
    comment_id = ObjectId()
    parent_id = ObjectId()
    ticket_id = ObjectId()
    existing_reply = {
        "_id": comment_id,
        "ticket_id": str(ticket_id),
        "author_id": str(author_id),
        "message": "before",
        "visibility": "public",
        "parent_id": str(parent_id),
    }
    current_user = {"_id": author_id, "role": "agent"}
    comments = fake_collection.__class__([existing_reply])
    monkeypatch.setattr(comment, "comments_collection", comments)
    _patch_comment_update_access(monkeypatch, existing_reply, {"role": "member"})
    validated = []
    monkeypatch.setattr(
        comment,
        "_validate_parent_comment",
        lambda **kwargs: validated.append(kwargs),
    )
    monkeypatch.setattr(comment, "_resolve_mentions", lambda **_kwargs: [])
    monkeypatch.setattr(comment, "_notify_mentions", lambda **_kwargs: 0)
    monkeypatch.setattr(comment, "notify_ticket_watchers", lambda **_kwargs: 0)
    monkeypatch.setattr(comment, "log_activity", lambda **_kwargs: None)

    response = comment.update_comment(
        str(comment_id),
        CommentUpdate(message="after"),
        current_user=current_user,
    )
    assert response["message"] == "Comment updated successfully"
    assert validated[0]["parent_id"] == str(parent_id)

    top_level = {
        **existing_reply,
        "_id": ObjectId(),
        "parent_id": None,
    }
    child = {
        "_id": ObjectId(),
        "ticket_id": str(ticket_id),
        "parent_id": str(top_level["_id"]),
    }
    monkeypatch.setattr(
        comment,
        "comments_collection",
        fake_collection.__class__([top_level, child]),
    )
    _patch_comment_update_access(monkeypatch, top_level, {"role": "member"})
    _assert_http_error(
        lambda: comment.update_comment(
            str(top_level["_id"]),
            CommentUpdate(visibility=CommentVisibility.INTERNAL),
            current_user=current_user,
        ),
        409,
        "A comment with replies cannot change visibility",
    )


def test_saved_filter_duplicate_key_is_reported_as_conflict(monkeypatch):
    class DuplicateCollection:
        @staticmethod
        def find_one(_query):
            return None

        @staticmethod
        def insert_one(_document):
            raise DuplicateKeyError("duplicate")

    monkeypatch.setattr(filters, "saved_filters_collection", DuplicateCollection())
    _assert_http_error(
        lambda: filters.create_saved_filter(
            SavedFilterCreate(name="Mine"),
            current_user={"_id": ObjectId()},
        ),
        409,
        "You already have a saved filter with this name",
    )


def test_notification_delivery_defaults_and_global_disable(fake_collection):
    assert notification_delivery.notification_is_enabled(
        user_id=ObjectId(),
        project_id=None,
        event_type="ticket_created",
        preferences_collection=fake_collection,
    )

    project_id = ObjectId()
    user_id = ObjectId()
    preferences = fake_collection.__class__(
        [
            {
                "project_id": str(project_id),
                "user_id": str(user_id),
                "enabled": False,
            }
        ]
    )
    assert not notification_delivery.notification_is_enabled(
        user_id=user_id,
        project_id=project_id,
        event_type="ticket_created",
        preferences_collection=preferences,
    )


def test_notification_error_and_bulk_read_paths(monkeypatch, fake_collection):
    _assert_http_error(
        lambda: notifications._parse_object_id("invalid", "notification_id"),
        400,
        "Invalid notification_id",
    )

    monkeypatch.setattr(
        notifications,
        "projects_collection",
        fake_collection.__class__(),
    )
    _assert_http_error(
        lambda: notifications._require_project_preference_access(
            str(ObjectId()),
            {"_id": ObjectId(), "role": "agent"},
        ),
        404,
        "Project not found",
    )

    user_id = ObjectId()
    other_id = ObjectId()
    notification_id = ObjectId()
    monkeypatch.setattr(
        notifications,
        "notifications_collection",
        fake_collection.__class__(),
    )
    _assert_http_error(
        lambda: notifications.mark_as_read(
            str(notification_id),
            current_user={"_id": user_id},
        ),
        404,
        "Notification not found",
    )

    monkeypatch.setattr(
        notifications,
        "notifications_collection",
        fake_collection.__class__([{"_id": notification_id, "user_id": str(other_id)}]),
    )
    _assert_http_error(
        lambda: notifications.mark_as_read(
            str(notification_id),
            current_user={"_id": user_id},
        ),
        403,
        "You can only mark your own notifications",
    )

    class BulkReadCollection:
        update = None

        @classmethod
        def update_many(cls, query, update):
            cls.update = (query, update)

    monkeypatch.setattr(
        notifications,
        "notifications_collection",
        BulkReadCollection,
    )
    response = notifications.mark_all_as_read(current_user={"_id": user_id})
    assert response == {"message": "All notifications marked as read"}
    assert BulkReadCollection.update[0]["user_id"] == str(user_id)


def test_permission_error_and_legacy_participant_paths(monkeypatch, fake_collection):
    _assert_http_error(
        lambda: permissions.ensure_role(None, {"admin"}),
        401,
        "User not authenticated",
    )
    _assert_http_error(
        lambda: permissions.parse_object_id("invalid", "thing_id"),
        400,
        "Invalid thing_id",
    )
    _assert_http_error(
        lambda: permissions.require_project_membership(
            ObjectId(),
            {"role": "agent"},
            fake_collection,
        ),
        401,
        "Authenticated user is missing an ID",
    )

    project_id = ObjectId()
    user_id = ObjectId()
    invalid_membership = fake_collection.__class__(
        [
            {
                "project_id": project_id,
                "user_id": user_id,
                "role": "invalid",
            }
        ]
    )
    _assert_http_error(
        lambda: permissions.require_project_membership(
            project_id,
            {"_id": user_id, "role": "agent"},
            invalid_membership,
        ),
        403,
        "Your project membership has an invalid role",
    )

    _assert_http_error(
        lambda: permissions.require_ticket_participant(
            {},
            {"_id": user_id, "role": "agent"},
            fake_collection,
        ),
        400,
        "Ticket is missing project_id",
    )

    missing_membership = HTTPException(
        status_code=403,
        detail="You are not a member of this project",
    )
    monkeypatch.setattr(
        permissions,
        "require_ticket_membership",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(missing_membership),
    )
    _assert_http_error(
        lambda: permissions.require_ticket_participant(
            {"project_id": str(project_id)},
            {"role": "agent"},
            fake_collection,
        ),
        401,
        "Authenticated user is missing an ID",
    )
    creator_access = permissions.require_ticket_participant(
        {"project_id": str(project_id), "userId": str(user_id)},
        {"_id": user_id, "role": "customer"},
        fake_collection,
    )
    assert creator_access["source"] == "ticket_creator"
    super_admin_access = permissions.require_ticket_participant(
        {"project_id": str(project_id)},
        {"_id": user_id, "role": "super_admin"},
        fake_collection,
    )
    assert super_admin_access["source"] == "global_role"


def _request(headers=None):
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": headers or [],
            "client": ("203.0.113.2", 1234),
            "server": ("testserver", 80),
        }
    )


def test_rate_limit_proxy_duplicate_and_dependency_paths(monkeypatch):
    monkeypatch.setattr(rate_limit.settings, "trust_proxy_headers", True)
    identifier = rate_limit._client_identifier(
        _request([(b"x-forwarded-for", b"198.51.100.8, 10.0.0.1")])
    )
    assert identifier == hashlib.sha256(b"198.51.100.8").hexdigest()

    class DuplicateThenSuccessCollection:
        calls = 0

        @classmethod
        def find_one_and_update(cls, *_args, **_kwargs):
            cls.calls += 1
            if cls.calls == 1:
                raise DuplicateKeyError("race")
            return {"count": 1}

    monkeypatch.setattr(
        rate_limit,
        "rate_limits_collection",
        DuplicateThenSuccessCollection,
    )
    monkeypatch.setattr(rate_limit.settings, "rate_limit_enabled", True)
    rate_limit.enforce_rate_limit(_request(), scope="login", max_requests=2)
    assert DuplicateThenSuccessCollection.calls == 2

    calls = []
    monkeypatch.setattr(
        rate_limit,
        "enforce_rate_limit",
        lambda request, **kwargs: calls.append((request, kwargs)),
    )
    dependency = rate_limit.rate_limit_dependency("scope", 3)
    request = _request()
    dependency(request)
    assert calls == [(request, {"scope": "scope", "max_requests": 3})]


def test_register_schema_rejects_global_role():
    with pytest.raises(ValidationError):
        UserRegister(
            name="Admin",
            email="admin@example.com",
            password="secret",
            role="admin",
        )


def test_default_status_workflow_moves_colliding_orders(monkeypatch, fake_collection):
    statuses = fake_collection.__class__(
        [{"_id": ObjectId(), "name": "Custom", "order": 10}]
    )
    monkeypatch.setattr(status, "statuses_collection", statuses)

    response = status.create_default_workflow_statuses(
        current_user={"_id": ObjectId(), "role": "super_admin"}
    )

    to_do = next(
        item for item in response["workflow_statuses"] if item["name"] == "To Do"
    )
    assert to_do["order"] != 10


def test_ticket_helper_edge_paths(monkeypatch, fake_collection):
    aware = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=5)))
    assert ticket._as_aware_utc(aware).tzinfo == timezone.utc

    transition_id = ObjectId()
    project_id = ObjectId()
    serialized = ticket._serialize_transition(
        {"_id": transition_id, "project_id": project_id}
    )
    assert serialized == {
        "_id": str(transition_id),
        "project_id": str(project_id),
    }

    monkeypatch.setattr(ticket, "ticket_collection", fake_collection.__class__())
    _assert_http_error(
        lambda: ticket._get_ticket_or_404(str(ObjectId())),
        404,
        "Ticket not found",
    )
    _assert_http_error(
        lambda: ticket._get_epic_or_404(str(ObjectId())),
        404,
        "Epic not found",
    )

    monkeypatch.setattr(ticket, "projects_collection", fake_collection.__class__())
    _assert_http_error(
        lambda: ticket._get_ticket_project({"project_id": str(project_id)}),
        404,
        "Project not found",
    )

    transition = {
        "_id": transition_id,
        "project_id": project_id,
        "from_status_ids": ["open"],
    }
    monkeypatch.setattr(
        ticket,
        "transitions_collection",
        fake_collection.__class__([transition]),
    )
    assert ticket._outgoing_transitions({"_id": project_id}, "open") == [transition]


def _transition_documents():
    project_id = ObjectId()
    source_id = ObjectId()
    target_id = ObjectId()
    ticket_id = ObjectId()
    ticket_doc = {
        "_id": ticket_id,
        "project_id": str(project_id),
        "current_status_id": str(source_id),
        "current_status_name": "Open",
        "assigned_agent": str(ObjectId()),
    }
    project = {
        "_id": project_id,
        "assigned_statuses": [str(source_id), str(target_id)],
    }
    transition = {
        "_id": ObjectId(),
        "project_id": project_id,
        "from_status_ids": [str(source_id)],
        "to_status_id": str(target_id),
        "name": "Resolve",
    }
    return ticket_id, ticket_doc, project, transition, target_id


def test_ticket_transition_target_and_concurrency_errors(
    monkeypatch,
    fake_collection,
):
    ticket_id, ticket_doc, project, transition, target_id = _transition_documents()
    current_user = {"_id": ObjectId(), "role": "agent"}
    monkeypatch.setattr(
        ticket,
        "require_ticket_membership",
        lambda *_args, **_kwargs: {"role": "member"},
    )

    unassigned_project = {
        **project,
        "assigned_statuses": project["assigned_statuses"][:1],
    }
    _assert_http_error(
        lambda: ticket._apply_ticket_transition(
            ticket_object_id=ticket_id,
            ticket=ticket_doc,
            project=unassigned_project,
            transition=transition,
            current_user=current_user,
        ),
        400,
        "Transition target status is not assigned to this project",
    )

    monkeypatch.setattr(ticket, "statuses_collection", fake_collection.__class__())
    _assert_http_error(
        lambda: ticket._apply_ticket_transition(
            ticket_object_id=ticket_id,
            ticket=ticket_doc,
            project=project,
            transition=transition,
            current_user=current_user,
        ),
        404,
        "Transition target status not found",
    )

    monkeypatch.setattr(
        ticket,
        "statuses_collection",
        fake_collection.__class__([{"_id": target_id, "name": "Resolved"}]),
    )
    monkeypatch.setattr(ticket, "_next_ticket_position", lambda *_args: 0)

    class ConcurrentTicketCollection:
        @staticmethod
        def update_one(_query, _update):
            return SimpleNamespace(matched_count=0)

    monkeypatch.setattr(
        ticket,
        "ticket_collection",
        ConcurrentTicketCollection(),
    )
    _assert_http_error(
        lambda: ticket._apply_ticket_transition(
            ticket_object_id=ticket_id,
            ticket=ticket_doc,
            project=project,
            transition=transition,
            current_user=current_user,
        ),
        409,
        "Ticket status changed before the transition could be applied",
    )


def test_transition_lookup_and_counter_race_paths(monkeypatch):
    ticket_id = ObjectId()
    transition_id = ObjectId()
    project_id = ObjectId()
    monkeypatch.setattr(
        ticket,
        "_get_ticket_or_404",
        lambda _ticket_id: (
            ticket_id,
            {"project_id": str(project_id)},
        ),
    )
    monkeypatch.setattr(
        ticket,
        "require_ticket_membership",
        lambda *_args, **_kwargs: {"role": "member"},
    )
    monkeypatch.setattr(
        ticket,
        "_get_ticket_project",
        lambda _ticket: (project_id, {"_id": project_id}),
    )
    monkeypatch.setattr(
        ticket,
        "transitions_collection",
        SimpleNamespace(find_one=lambda _query: None),
    )
    _assert_http_error(
        lambda: ticket._run_transition_by_id(
            str(ticket_id),
            str(transition_id),
            {"_id": ObjectId()},
        ),
        404,
        "Transition not found",
    )

    class RacingCounterCollection:
        @staticmethod
        def find_one(_query):
            return None

        @staticmethod
        def insert_one(_document):
            raise DuplicateKeyError("race")

        @staticmethod
        def find_one_and_update(*_args, **_kwargs):
            return {"counter": 4}

    monkeypatch.setattr(ticket, "counters_collection", RacingCounterCollection())
    monkeypatch.setattr(
        ticket,
        "_initial_project_ticket_counter",
        lambda _project: 3,
    )
    assert ticket._next_project_ticket_number({"_id": project_id, "code": "TEST"}) == 4


def _ticket_payload(project_id, **overrides):
    values = {
        "project_id": str(project_id),
        "title": "Coverage",
        "description": "Coverage branch",
        "category": Category.BUG,
        "issue_type": IssueType.TASK,
        "priority": Priority.LOW,
    }
    values.update(overrides)
    return TicketCreate(**values)


def test_create_ticket_epic_and_project_key_errors(monkeypatch, fake_collection):
    project_id = ObjectId()
    status_id = ObjectId()
    project_doc = {
        "_id": project_id,
        "code": "TEST",
        "assigned_statuses": [str(status_id)],
    }
    monkeypatch.setattr(
        ticket,
        "projects_collection",
        fake_collection.__class__([project_doc]),
    )
    monkeypatch.setattr(
        ticket,
        "require_project_membership",
        lambda *_args, **_kwargs: {"role": "member"},
    )
    current_user = {"_id": ObjectId(), "role": "agent"}
    epic_id = ObjectId()

    _assert_http_error(
        lambda: ticket.create_ticket(
            _ticket_payload(
                project_id,
                issue_type=IssueType.EPIC,
                epic_id=str(epic_id),
            ),
            current_user=current_user,
        ),
        400,
        "An epic cannot belong to another epic",
    )

    monkeypatch.setattr(
        ticket,
        "_get_epic_or_404",
        lambda _epic_id: (
            epic_id,
            {"project_id": str(ObjectId()), "issue_type": "epic"},
        ),
    )
    _assert_http_error(
        lambda: ticket.create_ticket(
            _ticket_payload(project_id, epic_id=str(epic_id)),
            current_user=current_user,
        ),
        400,
        "Epic must belong to the same project as the issue",
    )

    monkeypatch.setattr(
        ticket,
        "projects_collection",
        fake_collection.__class__(
            [{"_id": project_id, "assigned_statuses": [str(status_id)]}]
        ),
    )
    monkeypatch.setattr(
        ticket,
        "statuses_collection",
        fake_collection.__class__([{"_id": status_id, "name": "Open"}]),
    )
    _assert_http_error(
        lambda: ticket.create_ticket(
            _ticket_payload(project_id),
            current_user=current_user,
        ),
        400,
        "Project is missing a project key. Contact an administrator.",
    )


def test_epic_transition_listing_link_and_update_errors(
    monkeypatch,
    fake_collection,
):
    project_id = ObjectId()
    epic_id = ObjectId()
    user_id = ObjectId()
    monkeypatch.setattr(
        ticket,
        "_get_epic_or_404",
        lambda _epic_id: (
            epic_id,
            {"_id": epic_id, "project_id": str(project_id), "issue_type": "epic"},
        ),
    )
    monkeypatch.setattr(
        ticket,
        "require_ticket_membership",
        lambda *_args, **_kwargs: {"role": "member"},
    )
    monkeypatch.setattr(ticket, "projects_collection", fake_collection.__class__())
    _assert_http_error(
        lambda: ticket.get_epic(
            str(epic_id),
            current_user={"_id": user_id},
        ),
        404,
        "Project not found",
    )

    source_id = ObjectId()
    target_id = ObjectId()
    transition_id = ObjectId()
    ticket_doc = {
        "_id": ObjectId(),
        "project_id": str(project_id),
        "current_status_id": str(source_id),
        "assigned_agent": str(user_id),
    }
    project_doc = {
        "_id": project_id,
        "assigned_statuses": [str(source_id), str(target_id)],
    }
    transition_doc = {
        "_id": transition_id,
        "project_id": project_id,
        "from_status_ids": [str(source_id)],
        "to_status_id": str(target_id),
        "name": "Done",
    }
    monkeypatch.setattr(
        ticket,
        "_get_ticket_or_404",
        lambda _ticket_id: (ticket_doc["_id"], ticket_doc),
    )
    monkeypatch.setattr(
        ticket,
        "_get_ticket_project",
        lambda _ticket: (project_id, project_doc),
    )
    monkeypatch.setattr(
        ticket,
        "_outgoing_transitions",
        lambda *_args: [transition_doc],
    )
    monkeypatch.setattr(
        ticket,
        "statuses_collection",
        fake_collection.__class__([{"_id": target_id, "name": "Done"}]),
    )
    response = ticket.get_ticket_transitions(
        str(ticket_doc["_id"]),
        current_user={"_id": user_id},
    )
    assert response["transitions"][0]["available"] is True
    assert response["transitions"][0]["project_id"] == str(project_id)

    missing_target_id = ObjectId()
    monkeypatch.setattr(
        ticket,
        "ticket_collection",
        fake_collection.__class__(),
    )
    _assert_http_error(
        lambda: ticket.create_ticket_link(
            str(ticket_doc["_id"]),
            IssueLinkCreate(
                to_ticket_id=str(missing_target_id),
                link_type=IssueLinkType.BLOCKS,
            ),
            current_user={"_id": user_id},
        ),
        404,
        "Target ticket not found",
    )

    sprint_id = ObjectId()
    update_ticket_id = ObjectId()
    update_ticket = {
        "_id": update_ticket_id,
        "project_id": str(project_id),
        "sprint_id": str(sprint_id),
    }
    monkeypatch.setattr(
        ticket,
        "ticket_collection",
        fake_collection.__class__([update_ticket]),
    )
    _assert_http_error(
        lambda: ticket.update_ticket(
            str(update_ticket_id),
            TicketUpdate(),
            current_user={"_id": user_id},
        ),
        400,
        "No ticket fields supplied",
    )
    monkeypatch.setattr(
        ticket,
        "sprints_collection",
        fake_collection.__class__([{"_id": sprint_id, "status": "completed"}]),
    )
    _assert_http_error(
        lambda: ticket.update_ticket(
            str(update_ticket_id),
            TicketUpdate(story_points=5),
            current_user={"_id": user_id},
        ),
        409,
        "Completed sprint tickets and estimates are locked",
    )


def test_ticket_summary_missing_and_inaccessible_project_paths(
    monkeypatch,
    fake_collection,
):
    project_id = ObjectId()
    monkeypatch.setattr(ticket, "projects_collection", fake_collection.__class__())
    _assert_http_error(
        lambda: ticket.get_tickets_summary(
            project_id=str(project_id),
            current_user={"_id": ObjectId()},
        ),
        404,
        "Project not found",
    )

    monkeypatch.setattr(
        ticket,
        "get_accessible_project_ids",
        lambda *_args: [],
    )
    assert (
        ticket.get_tickets_summary(
            project_id=None,
            current_user={"_id": ObjectId()},
        )
        == {}
    )
