from app.api.notification_stream import notification_broker
from app.models.notifications import Notifications


def notification_is_enabled(
    *,
    user_id,
    project_id,
    event_type: str,
    preferences_collection,
) -> bool:
    """Return the user's effective delivery choice for a project event."""
    if project_id is None:
        return True

    preference = preferences_collection.find_one(
        {
            "user_id": str(user_id),
            "project_id": str(project_id),
        }
    )
    if not preference:
        return True
    if preference.get("enabled", True) is False:
        return False

    disabled_event_types = set(preference.get("disabled_event_types") or [])
    return event_type not in disabled_event_types


def create_notification(
    *,
    ticket_id,
    user_id,
    event_type: str,
    notifications_collection,
    preferences_collection,
    project_id=None,
    comment_id=None,
    actor_id=None,
):
    """Insert one notification when the recipient has not opted out."""
    if not notification_is_enabled(
        user_id=user_id,
        project_id=project_id,
        event_type=event_type,
        preferences_collection=preferences_collection,
    ):
        return None

    notification = Notifications(
        project_id=str(project_id) if project_id is not None else None,
        ticket_id=str(ticket_id),
        comment_id=str(comment_id) if comment_id is not None else None,
        user_id=str(user_id),
        actor_id=str(actor_id) if actor_id is not None else None,
        event_type=event_type,
    )
    document = notification.model_dump(exclude_none=True)
    result = notifications_collection.insert_one(document)
    document["_id"] = result.inserted_id
    notification_broker.publish(document)
    return result
