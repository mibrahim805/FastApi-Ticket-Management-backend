from app.api.notification_delivery import create_notification


def notify_ticket_watchers(
    *,
    ticket: dict,
    ticket_id: str,
    event_type: str,
    actor_id,
    notifications_collection,
    preferences_collection,
    comment_id: str | None = None,
) -> int:
    """Notify every watcher except the user who triggered the event."""
    actor_id = str(actor_id)
    notified = 0
    for watcher_id in dict.fromkeys(map(str, ticket.get("watchers") or [])):
        if watcher_id == actor_id:
            continue
        result = create_notification(
            ticket_id=ticket_id,
            project_id=ticket.get("project_id"),
            comment_id=comment_id,
            user_id=watcher_id,
            actor_id=actor_id,
            event_type=event_type,
            notifications_collection=notifications_collection,
            preferences_collection=preferences_collection,
        )
        if result is not None:
            notified += 1
    return notified
