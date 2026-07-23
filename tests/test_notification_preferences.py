import pytest
from bson import ObjectId
from fastapi import HTTPException
from pydantic import ValidationError

from app.api import notifications
from app.api.notification_delivery import create_notification
from app.api.watcher_notifications import notify_ticket_watchers
from app.main import app
from app.models.notifications import NotificationPreferenceUpdate


def test_notification_preferences_default_to_enabled(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    user_id = ObjectId()
    monkeypatch.setattr(
        notifications,
        "projects_collection",
        fake_collection.__class__([{"_id": project_id}]),
    )
    grant_project_role(project_id, user_id, "member")

    response = notifications.get_notification_preferences(
        str(project_id),
        current_user={"_id": user_id, "role": "agent"},
    )

    assert response["user_id"] == str(user_id)
    assert response["project_id"] == str(project_id)
    assert response["enabled"] is True
    assert response["disabled_event_types"] == []
    assert "ticket_transitioned" in response["available_event_types"]
    assert "comment_mentioned" in response["available_event_types"]


def test_user_can_replace_project_notification_preferences(
    monkeypatch,
    fake_collection,
    notification_preferences,
    grant_project_role,
):
    project_id = ObjectId()
    user_id = ObjectId()
    monkeypatch.setattr(
        notifications,
        "projects_collection",
        fake_collection.__class__([{"_id": project_id}]),
    )
    grant_project_role(project_id, user_id, "reporter")

    response = notifications.update_notification_preferences(
        str(project_id),
        NotificationPreferenceUpdate(
            disabled_event_types={"comment_created", "ticket_transitioned"},
        ),
        current_user={"_id": user_id, "role": "customer"},
    )

    assert response["enabled"] is True
    assert response["disabled_event_types"] == [
        "comment_created",
        "ticket_transitioned",
    ]
    assert len(notification_preferences.documents) == 1

    replacement = notifications.update_notification_preferences(
        str(project_id),
        NotificationPreferenceUpdate(enabled=False),
        current_user={"_id": user_id, "role": "customer"},
    )

    assert replacement["enabled"] is False
    assert replacement["disabled_event_types"] == []
    assert len(notification_preferences.documents) == 1


def test_non_member_cannot_manage_project_notification_preferences(
    monkeypatch,
    fake_collection,
):
    project_id = ObjectId()
    monkeypatch.setattr(
        notifications,
        "projects_collection",
        fake_collection.__class__([{"_id": project_id}]),
    )

    with pytest.raises(HTTPException) as exc_info:
        notifications.get_notification_preferences(
            str(project_id),
            current_user={"_id": ObjectId(), "role": "agent"},
        )

    assert exc_info.value.status_code == 403


def test_notification_preferences_reject_unknown_event_types():
    with pytest.raises(ValidationError):
        NotificationPreferenceUpdate(disabled_event_types={"unknown_event"})


def test_preference_aware_delivery_suppresses_only_disabled_event(
    fake_collection,
):
    project_id = ObjectId()
    user_id = ObjectId()
    stored_notifications = fake_collection.__class__()
    preferences = fake_collection.__class__(
        [
            {
                "project_id": str(project_id),
                "user_id": str(user_id),
                "enabled": True,
                "disabled_event_types": ["ticket_transitioned"],
            }
        ]
    )

    suppressed = create_notification(
        project_id=project_id,
        ticket_id=ObjectId(),
        user_id=user_id,
        event_type="ticket_transitioned",
        notifications_collection=stored_notifications,
        preferences_collection=preferences,
    )
    delivered = create_notification(
        project_id=project_id,
        ticket_id=ObjectId(),
        user_id=user_id,
        event_type="comment_created",
        notifications_collection=stored_notifications,
        preferences_collection=preferences,
    )

    assert suppressed is None
    assert delivered is not None
    assert len(stored_notifications.documents) == 1
    assert stored_notifications.documents[0]["event_type"] == "comment_created"
    assert stored_notifications.documents[0]["is_read"] is False


def test_status_transition_watcher_notification_respects_preference(
    fake_collection,
):
    project_id = ObjectId()
    watcher_id = ObjectId()
    actor_id = ObjectId()
    stored_notifications = fake_collection.__class__()
    preferences = fake_collection.__class__(
        [
            {
                "project_id": str(project_id),
                "user_id": str(watcher_id),
                "enabled": True,
                "disabled_event_types": ["ticket_transitioned"],
            }
        ]
    )

    notified = notify_ticket_watchers(
        ticket={
            "project_id": str(project_id),
            "watchers": [str(watcher_id)],
        },
        ticket_id=str(ObjectId()),
        event_type="ticket_transitioned",
        actor_id=actor_id,
        notifications_collection=stored_notifications,
        preferences_collection=preferences,
    )

    assert notified == 0
    assert stored_notifications.documents == []


def test_notification_preference_routes_are_exposed_in_openapi():
    paths = app.openapi()["paths"]

    assert set(paths["/notifications/preferences/{project_id}"]) == {"get", "put"}
