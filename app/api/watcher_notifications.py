from datetime import datetime, timezone


def notify_ticket_watchers(
    *,
    ticket: dict,
    ticket_id: str,
    event_type: str,
    actor_id,
    notifications_collection,
    comment_id: str | None = None,
) -> int:
    """Notify every watcher except the user who triggered the event."""
    actor_id = str(actor_id)
    notified = 0
    for watcher_id in dict.fromkeys(map(str, ticket.get("watchers") or [])):
        if watcher_id == actor_id:
            continue
        notification = {
            "ticket_id": ticket_id,
            "user_id": watcher_id,
            "event_type": event_type,
            "created_at": datetime.now(timezone.utc),
            "is_read": False,
        }
        if comment_id is not None:
            notification["comment_id"] = comment_id
        notifications_collection.insert_one(notification)
        notified += 1
    return notified
