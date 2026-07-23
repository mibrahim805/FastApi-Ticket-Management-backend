from datetime import datetime, timezone

import pytest
from bson import ObjectId
from fastapi import HTTPException
from pydantic import ValidationError

from app.api import comment
from app.main import app
from app.models.comment import Comment, CommentReply, CommentUpdate


def _ticket(project_id, ticket_id, **overrides):
    return {
        "_id": ticket_id,
        "project_id": str(project_id),
        "userId": str(ObjectId()),
        "assigned_agent": None,
        "watchers": [],
        **overrides,
    }


def test_assigned_agent_can_comment_on_legacy_ticket_without_membership(
    monkeypatch,
    fake_collection,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    agent_id = ObjectId()
    tickets = fake_collection.__class__(
        [
            _ticket(
                project_id,
                ticket_id,
                assigned_agent=str(agent_id),
            )
        ]
    )
    comments = fake_collection.__class__()
    monkeypatch.setattr(comment, "ticket_collection", tickets)
    monkeypatch.setattr(comment, "comments_collection", comments)
    monkeypatch.setattr(
        comment,
        "notifications_collection",
        fake_collection.__class__(),
    )

    response = comment.create_comment(
        str(ticket_id),
        Comment(message="I am working on this", visibility="internal"),
        current_user={"_id": agent_id, "role": "agent"},
    )

    assert response["comment_id"] == str(comments.documents[0]["_id"])
    assert comments.documents[0]["author_id"] == str(agent_id)
    assert comments.documents[0]["visibility"] == "internal"


def test_comment_mentions_notify_each_visible_user_once(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    actor_id = ObjectId()
    alice_id = ObjectId()
    bob_id = ObjectId()
    users = fake_collection.__class__(
        [
            {"_id": alice_id, "name": "Alice", "is_active": True},
            {
                "_id": bob_id,
                "username": "bob.smith",
                "email": "bob@example.com",
                "is_active": True,
            },
        ]
    )
    comments = fake_collection.__class__()
    notifications = fake_collection.__class__()
    monkeypatch.setattr(
        comment,
        "ticket_collection",
        fake_collection.__class__([_ticket(project_id, ticket_id)]),
    )
    monkeypatch.setattr(comment, "comments_collection", comments)
    monkeypatch.setattr(comment, "notifications_collection", notifications)
    monkeypatch.setattr(comment, "user_collection", users)
    grant_project_role(project_id, actor_id, "member")
    grant_project_role(project_id, alice_id, "member")
    grant_project_role(project_id, bob_id, "member")

    comment.create_comment(
        str(ticket_id),
        Comment(
            message=(
                "@Alice, please pair with @bob.smith. "
                "Thanks @ALICE; mail dev@example.com separately."
            ),
            visibility="public",
        ),
        current_user={"_id": actor_id, "role": "agent"},
    )

    mention_notifications = [
        item
        for item in notifications.documents
        if item["event_type"] == comment.MENTION_EVENT_TYPE
    ]
    assert {item["user_id"] for item in mention_notifications} == {
        str(alice_id),
        str(bob_id),
    }
    assert len(mention_notifications) == 2
    assert comments.documents[0]["mentioned_user_ids"] == [
        str(alice_id),
        str(bob_id),
    ]
    assert all(item["is_read"] is False for item in mention_notifications)


def test_internal_comment_rejects_mention_of_reporter(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    actor_id = ObjectId()
    reporter_id = ObjectId()
    comments = fake_collection.__class__()
    notifications = fake_collection.__class__()
    monkeypatch.setattr(
        comment,
        "ticket_collection",
        fake_collection.__class__([_ticket(project_id, ticket_id)]),
    )
    monkeypatch.setattr(comment, "comments_collection", comments)
    monkeypatch.setattr(comment, "notifications_collection", notifications)
    monkeypatch.setattr(
        comment,
        "user_collection",
        fake_collection.__class__(
            [{"_id": reporter_id, "name": "Customer", "is_active": True}]
        ),
    )
    grant_project_role(project_id, actor_id, "member")
    grant_project_role(project_id, reporter_id, "reporter")

    with pytest.raises(HTTPException) as exc_info:
        comment.create_comment(
            str(ticket_id),
            Comment(message="Internal note for @Customer", visibility="internal"),
            current_user={"_id": actor_id, "role": "agent"},
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == (
        "Mentioned user '@customer' cannot view internal comments"
    )
    assert comments.documents == []
    assert notifications.documents == []


def test_reply_inherits_visibility_and_threaded_listing_nests_it(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    author_id = ObjectId()
    parent_id = ObjectId()
    comments = fake_collection.__class__(
        [
            {
                "_id": parent_id,
                "ticket_id": str(ticket_id),
                "author_id": str(author_id),
                "message": "Root",
                "visibility": "internal",
                "parent_id": None,
                "created_at": datetime.now(timezone.utc),
            }
        ]
    )
    monkeypatch.setattr(
        comment,
        "ticket_collection",
        fake_collection.__class__([_ticket(project_id, ticket_id)]),
    )
    monkeypatch.setattr(comment, "comments_collection", comments)
    monkeypatch.setattr(
        comment,
        "notifications_collection",
        fake_collection.__class__(),
    )
    grant_project_role(project_id, author_id, "member")

    response = comment.create_reply(
        str(parent_id),
        CommentReply(message="Nested response"),
        current_user={"_id": author_id, "role": "agent"},
    )
    threads = comment.get_comments(
        str(ticket_id),
        current_user={"_id": author_id, "role": "agent"},
        threaded=True,
    )

    assert response["comment"]["parent_id"] == str(parent_id)
    assert response["comment"]["visibility"] == "internal"
    assert len(threads) == 1
    assert threads[0]["message"] == "Root"
    assert [reply["message"] for reply in threads[0]["replies"]] == ["Nested response"]


def test_top_level_comment_payload_does_not_accept_parent_id():
    with pytest.raises(ValidationError):
        Comment(
            message="This is a top-level comment",
            visibility="public",
            parent_id=str(ObjectId()),
        )


def test_author_can_edit_comment_and_only_new_mentions_are_notified(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    author_id = ObjectId()
    alice_id = ObjectId()
    bob_id = ObjectId()
    comment_id = ObjectId()
    comments = fake_collection.__class__(
        [
            {
                "_id": comment_id,
                "ticket_id": str(ticket_id),
                "author_id": str(author_id),
                "message": "Hello @Alice",
                "visibility": "public",
                "parent_id": None,
                "mentioned_user_ids": [str(alice_id)],
            }
        ]
    )
    notifications = fake_collection.__class__()
    monkeypatch.setattr(
        comment,
        "ticket_collection",
        fake_collection.__class__([_ticket(project_id, ticket_id)]),
    )
    monkeypatch.setattr(comment, "comments_collection", comments)
    monkeypatch.setattr(comment, "notifications_collection", notifications)
    monkeypatch.setattr(
        comment,
        "user_collection",
        fake_collection.__class__(
            [
                {"_id": alice_id, "name": "Alice"},
                {"_id": bob_id, "name": "Bob"},
            ]
        ),
    )
    grant_project_role(project_id, author_id, "member")
    grant_project_role(project_id, alice_id, "member")
    grant_project_role(project_id, bob_id, "member")

    response = comment.update_comment(
        str(comment_id),
        CommentUpdate(message="Hello @Alice and @Bob"),
        current_user={"_id": author_id, "role": "agent"},
    )

    assert response == {"message": "Comment updated successfully"}
    assert comments.documents[0]["message"] == "Hello @Alice and @Bob"
    mention_notifications = [
        item
        for item in notifications.documents
        if item["event_type"] == comment.MENTION_EVENT_TYPE
    ]
    assert [item["user_id"] for item in mention_notifications] == [str(bob_id)]


def test_update_is_not_written_when_mentioned_user_is_not_project_member(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    author_id = ObjectId()
    agent_id = ObjectId()
    comment_id = ObjectId()
    original_message = "Original comment"
    comments = fake_collection.__class__(
        [
            {
                "_id": comment_id,
                "ticket_id": str(ticket_id),
                "author_id": str(author_id),
                "message": original_message,
                "visibility": "public",
                "parent_id": None,
                "mentioned_user_ids": [],
            }
        ]
    )
    notifications = fake_collection.__class__()
    monkeypatch.setattr(
        comment,
        "ticket_collection",
        fake_collection.__class__([_ticket(project_id, ticket_id)]),
    )
    monkeypatch.setattr(comment, "comments_collection", comments)
    monkeypatch.setattr(comment, "notifications_collection", notifications)
    monkeypatch.setattr(
        comment,
        "user_collection",
        fake_collection.__class__(
            [{"_id": agent_id, "name": "agent", "is_active": True}]
        ),
    )
    grant_project_role(project_id, author_id, "lead")

    with pytest.raises(HTTPException) as exc_info:
        comment.update_comment(
            str(comment_id),
            CommentUpdate(message="@agent please review this comment"),
            current_user={"_id": author_id, "role": "customer"},
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == (
        "Mentioned user '@agent' is not a member of this project"
    )
    assert comments.documents[0]["message"] == original_message
    assert comments.documents[0]["mentioned_user_ids"] == []
    assert comments.updated_documents == []
    assert notifications.documents == []


def test_non_author_cannot_edit_or_delete_comment(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    author_id = ObjectId()
    other_member_id = ObjectId()
    comment_id = ObjectId()
    monkeypatch.setattr(
        comment,
        "ticket_collection",
        fake_collection.__class__([_ticket(project_id, ticket_id)]),
    )
    monkeypatch.setattr(
        comment,
        "comments_collection",
        fake_collection.__class__(
            [
                {
                    "_id": comment_id,
                    "ticket_id": str(ticket_id),
                    "author_id": str(author_id),
                    "message": "Original",
                    "visibility": "public",
                }
            ]
        ),
    )
    grant_project_role(project_id, other_member_id, "member")
    current_user = {"_id": other_member_id, "role": "agent"}

    with pytest.raises(HTTPException) as edit_error:
        comment.update_comment(
            str(comment_id),
            CommentUpdate(message="Changed"),
            current_user=current_user,
        )
    with pytest.raises(HTTPException) as delete_error:
        comment.delete_comment(str(comment_id), current_user=current_user)

    assert edit_error.value.status_code == 403
    assert delete_error.value.status_code == 403


def test_deleting_parent_comment_preserves_and_reparents_replies(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    author_id = ObjectId()
    parent_id = ObjectId()
    child_id = ObjectId()
    grandchild_id = ObjectId()
    unrelated_id = ObjectId()
    comments = fake_collection.__class__(
        [
            {
                "_id": parent_id,
                "ticket_id": str(ticket_id),
                "author_id": str(author_id),
                "message": "Root",
                "visibility": "public",
            },
            {
                "_id": child_id,
                "ticket_id": str(ticket_id),
                "author_id": str(ObjectId()),
                "message": "Child",
                "visibility": "public",
                "parent_id": str(parent_id),
            },
            {
                "_id": grandchild_id,
                "ticket_id": str(ticket_id),
                "author_id": str(ObjectId()),
                "message": "Grandchild",
                "visibility": "public",
                "parent_id": str(child_id),
            },
            {
                "_id": unrelated_id,
                "ticket_id": str(ticket_id),
                "author_id": str(author_id),
                "message": "Keep me",
                "visibility": "public",
            },
        ]
    )
    monkeypatch.setattr(
        comment,
        "ticket_collection",
        fake_collection.__class__([_ticket(project_id, ticket_id)]),
    )
    monkeypatch.setattr(comment, "comments_collection", comments)
    monkeypatch.setattr(
        comment,
        "notifications_collection",
        fake_collection.__class__(),
    )
    grant_project_role(project_id, author_id, "member")

    response = comment.delete_comment(
        str(parent_id),
        current_user={"_id": author_id, "role": "agent"},
    )

    assert response["reparented_reply_count"] == 1
    assert [item["_id"] for item in comments.documents] == [
        child_id,
        grandchild_id,
        unrelated_id,
    ]
    assert comments.documents[0]["parent_id"] is None
    assert comments.documents[1]["parent_id"] == str(child_id)


def test_comment_collaboration_routes_are_exposed_in_openapi():
    paths = app.openapi()["paths"]

    assert "/tickets/{ticket_id}/comments" in paths
    assert {"post", "get"} <= set(paths["/tickets/{ticket_id}/comments"])
    assert "/comments/{parent_comment_id}/replies" in paths
    assert "/comments/{comment_id}" in paths
    assert "patch" in paths["/comments/{comment_id}"]
    assert "delete" in paths["/comments/{comment_id}"]
