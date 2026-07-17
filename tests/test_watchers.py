import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.api import comment, ticket
from app.main import app
from app.models.comment import Comment


def test_subscribe_and_unsubscribe_are_idempotent(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    user_id = ObjectId()
    tickets = fake_collection.__class__(
        [
            {
                "_id": ticket_id,
                "project_id": str(project_id),
                "watchers": [],
            }
        ]
    )
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    grant_project_role(project_id, user_id, "reporter")
    current_user = {"_id": user_id, "role": "customer"}

    first = ticket.subscribe_to_ticket(str(ticket_id), current_user=current_user)
    second = ticket.subscribe_to_ticket(str(ticket_id), current_user=current_user)

    assert first["watchers"] == [str(user_id)]
    assert second["watchers"] == [str(user_id)]

    first_unsubscribe = ticket.unsubscribe_from_ticket(
        str(ticket_id),
        current_user=current_user,
    )
    second_unsubscribe = ticket.unsubscribe_from_ticket(
        str(ticket_id),
        current_user=current_user,
    )

    assert first_unsubscribe["watchers"] == []
    assert second_unsubscribe["watchers"] == []


def test_non_member_cannot_subscribe(monkeypatch, fake_collection):
    project_id = ObjectId()
    ticket_id = ObjectId()
    monkeypatch.setattr(
        ticket,
        "ticket_collection",
        fake_collection.__class__([{"_id": ticket_id, "project_id": str(project_id)}]),
    )

    with pytest.raises(HTTPException) as exc_info:
        ticket.subscribe_to_ticket(
            str(ticket_id),
            current_user={"_id": ObjectId(), "role": "user"},
        )

    assert exc_info.value.status_code == 403


def test_ticket_transition_notifies_watchers_except_actor(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    open_status_id = ObjectId()
    done_status_id = ObjectId()
    actor_id = ObjectId()
    watcher_id = ObjectId()
    projects = fake_collection.__class__(
        [
            {
                "_id": project_id,
                "assigned_statuses": [
                    str(open_status_id),
                    str(done_status_id),
                ],
                "workflow_transitions": [
                    {
                        "name": "Complete",
                        "from_status_id": str(open_status_id),
                        "to_status_id": str(done_status_id),
                        "guards": {
                            "allowed_roles": ["member"],
                            "requires_assignee": False,
                        },
                    }
                ],
            }
        ]
    )
    statuses = fake_collection.__class__([{"_id": done_status_id, "name": "Done"}])
    tickets = fake_collection.__class__(
        [
            {
                "_id": ticket_id,
                "project_id": str(project_id),
                "ticket_code": "WATCH-1",
                "current_status_id": str(open_status_id),
                "current_status_name": "Open",
                "watchers": [str(watcher_id), str(actor_id)],
            }
        ]
    )
    notifications = fake_collection.__class__()
    monkeypatch.setattr(ticket, "projects_collection", projects)
    monkeypatch.setattr(ticket, "statuses_collection", statuses)
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "notifications_collection", notifications)
    grant_project_role(project_id, actor_id, "member")

    ticket.transition_ticket(
        str(ticket_id),
        ticket.TicketTransitionRequest(transition_name="Complete"),
        current_user={"_id": actor_id, "role": "agent"},
    )

    assert len(notifications.documents) == 1
    assert notifications.documents[0]["user_id"] == str(watcher_id)
    assert notifications.documents[0]["event_type"] == "ticket_transitioned"
    assert notifications.documents[0]["is_read"] is False


def test_comment_notifies_ticket_watchers(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    actor_id = ObjectId()
    watcher_id = ObjectId()
    tickets = fake_collection.__class__(
        [
            {
                "_id": ticket_id,
                "project_id": str(project_id),
                "watchers": [str(watcher_id), str(actor_id)],
            }
        ]
    )
    comments = fake_collection.__class__()
    notifications = fake_collection.__class__()
    monkeypatch.setattr(comment, "ticket_collection", tickets)
    monkeypatch.setattr(comment, "comments_collection", comments)
    monkeypatch.setattr(comment, "notifications_collection", notifications)
    grant_project_role(project_id, actor_id, "member")

    comment.create_comment(
        str(ticket_id),
        Comment(message="A useful update", visibility="public"),
        current_user={"_id": actor_id, "role": "agent"},
    )

    watcher_notifications = [
        item for item in notifications.documents if item["user_id"] == str(watcher_id)
    ]
    assert len(watcher_notifications) == 1
    assert watcher_notifications[0]["event_type"] == "comment_created"
    assert watcher_notifications[0]["comment_id"] == str(comments.documents[0]["_id"])


def test_watcher_routes_are_exposed_in_openapi():
    paths = app.openapi()["paths"]

    assert "/tickets/{ticket_id}/watchers/subscribe" in paths
    assert "/tickets/{ticket_id}/watchers/unsubscribe" in paths
