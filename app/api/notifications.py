import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pymongo import ReturnDocument

from app.api.auth import get_current_user
from app.api.notification_stream import notification_broker
from app.api.permissions import (
    Capability,
    require_project_capability,
)
from app.db.db import (
    notification_preferences_collection,
    notifications_collection,
    project_members_collection,
    projects_collection,
)
from app.models.notifications import (
    NOTIFICATION_EVENT_TYPES,
    NotificationPreferenceUpdate,
)

router = APIRouter(prefix="/notifications", tags=["Notifications"])

SSE_HEARTBEAT_INTERVAL_SECONDS = 15
SSE_BATCH_SIZE = 100


def _parse_object_id(value: str, field_name: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}")


def _require_project_preference_access(
    project_id: str,
    current_user: dict,
) -> str:
    project_object_id = _parse_object_id(project_id, "project_id")
    if not projects_collection.find_one({"_id": project_object_id}):
        raise HTTPException(status_code=404, detail="Project not found")
    require_project_capability(
        project_object_id,
        current_user,
        project_members_collection,
        Capability.MANAGE_NOTIFICATION_PREFERENCES,
        detail="Only project members can manage notification preferences",
        allow_super_admin=True,
    )
    return str(project_object_id)


def _serialize_preference(preference: dict) -> dict:
    serialized = dict(preference)
    serialized.pop("_id", None)
    serialized["disabled_event_types"] = sorted(
        serialized.get("disabled_event_types") or []
    )
    serialized["available_event_types"] = sorted(NOTIFICATION_EVENT_TYPES)
    return serialized


def _notification_cursor(notification: dict) -> tuple:
    """Return the position used to order notification stream events."""
    return notification.get("created_at"), str(notification["_id"])


def _notification_sort_key(notification: dict) -> tuple[str, str]:
    created_at = notification.get("created_at")
    if isinstance(created_at, datetime):
        created_at = created_at.astimezone(timezone.utc).isoformat()
    return str(created_at or ""), str(notification["_id"])


def _get_stream_start_cursor(
    user_id: str,
    last_event_id: Optional[str],
) -> Optional[tuple]:
    """Find the event position from which an SSE connection should continue."""
    if last_event_id:
        try:
            last_notification = notifications_collection.find_one(
                {"_id": ObjectId(last_event_id), "user_id": user_id}
            )
        except (InvalidId, TypeError):
            last_notification = None

        if last_notification:
            return _notification_cursor(last_notification)

    # A new connection starts after the current latest notification. This
    # prevents the whole notification history from being replayed on connect.
    latest = list(
        notifications_collection.find({"user_id": user_id})
        .sort("created_at", -1)
        .limit(1)
    )
    if not latest:
        return None

    latest_created_at = latest[0].get("created_at")
    same_timestamp = list(
        notifications_collection.find(
            {"user_id": user_id, "created_at": latest_created_at}
        )
        .sort("_id", -1)
        .limit(1)
    )
    return _notification_cursor(same_timestamp[0] if same_timestamp else latest[0])


def _find_notifications_after(user_id: str, cursor: Optional[tuple]) -> list[dict]:
    query = {"user_id": user_id}
    if cursor is not None:
        created_at, notification_id = cursor
        query["$or"] = [
            {"created_at": {"$gt": created_at}},
            {"created_at": created_at, "_id": {"$gt": ObjectId(notification_id)}},
        ]

    notifications = list(
        notifications_collection.find(query).sort("created_at", 1).limit(SSE_BATCH_SIZE)
    )
    return sorted(notifications, key=_notification_sort_key)


def _json_default(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, ObjectId):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _format_sse_notification(notification: dict) -> str:
    event_id = str(notification["_id"])
    payload = dict(notification)
    payload["_id"] = event_id
    data = json.dumps(payload, default=_json_default, separators=(",", ":"))
    return f"id: {event_id}\nevent: notification\ndata: {data}\n\n"


async def _notification_event_stream(
    request: Request,
    user_id: str,
    last_event_id: Optional[str],
):
    async with notification_broker.subscribe(user_id) as subscription:
        cursor = await asyncio.to_thread(
            _get_stream_start_cursor,
            user_id,
            last_event_id,
        )

        # Read missed events on connect. New inserts are delivered by the
        # subscription, so idle connections do not poll MongoDB.
        while True:
            pending = await asyncio.to_thread(
                _find_notifications_after,
                user_id,
                cursor,
            )
            for notification in pending:
                if await request.is_disconnected():
                    return
                cursor = _notification_cursor(notification)
                yield _format_sse_notification(notification)
            if len(pending) < SSE_BATCH_SIZE:
                break

        while not await request.is_disconnected():
            try:
                notification = await asyncio.wait_for(
                    subscription.get(),
                    timeout=SSE_HEARTBEAT_INTERVAL_SECONDS,
                )
            except TimeoutError:
                if not await request.is_disconnected():
                    yield ": keep-alive\n\n"
                continue

            if not _is_notification_after(notification, cursor):
                continue
            cursor = _notification_cursor(notification)
            yield _format_sse_notification(notification)


def _is_notification_after(notification: dict, cursor: Optional[tuple]) -> bool:
    if cursor is None:
        return True
    return _notification_sort_key(notification) > _notification_sort_key(
        {"_id": cursor[1], "created_at": cursor[0]}
    )


@router.get("/stream", tags=["Notifications"])
async def stream_notifications(
    request: Request,
    current_user: dict = Depends(get_current_user),
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
):
    """Stream newly created notifications for the authenticated user."""
    return StreamingResponse(
        _notification_event_stream(
            request,
            str(current_user["_id"]),
            last_event_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/", tags=["Notifications"])
def list_notifications(
    current_user: dict = Depends(get_current_user),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(20, ge=1, le=100, description="Results per page"),
    unread_only: Optional[bool] = Query(
        None, description="Filter to show only unread notifications"
    ),
):
    query = {"user_id": str(current_user["_id"])}

    if unread_only:
        query["is_read"] = False

    skip = (page - 1) * page_size
    limit = page_size

    total_count = notifications_collection.count_documents(query)

    notifications = list(
        notifications_collection.find(query)
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
    )

    for notification in notifications:
        notification["_id"] = str(notification["_id"])

    total_pages = (total_count + page_size - 1) // page_size

    return {
        "data": notifications,
        "pagination": {
            "total": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
        },
    }


@router.get("/preferences/{project_id}")
def get_notification_preferences(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    canonical_project_id = _require_project_preference_access(
        project_id,
        current_user,
    )
    user_id = str(current_user["_id"])
    preference = notification_preferences_collection.find_one(
        {
            "user_id": user_id,
            "project_id": canonical_project_id,
        }
    )
    if not preference:
        preference = {
            "user_id": user_id,
            "project_id": canonical_project_id,
            "enabled": True,
            "disabled_event_types": [],
        }
    return _serialize_preference(preference)


@router.put("/preferences/{project_id}")
def update_notification_preferences(
    project_id: str,
    payload: NotificationPreferenceUpdate,
    current_user: dict = Depends(get_current_user),
):
    canonical_project_id = _require_project_preference_access(
        project_id,
        current_user,
    )
    query = {
        "user_id": str(current_user["_id"]),
        "project_id": canonical_project_id,
    }
    now = datetime.now(timezone.utc)
    update_fields = {
        "enabled": payload.enabled,
        "disabled_event_types": sorted(payload.disabled_event_types),
        "updated_at": now,
    }
    if not notification_preferences_collection.find_one(query):
        update_fields["created_at"] = now

    preference = notification_preferences_collection.find_one_and_update(
        query,
        {"$set": update_fields},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return _serialize_preference(preference)


@router.get("/{notification_id}", tags=["Notifications"])
def get_notification(
    notification_id: str,
    current_user: dict = Depends(get_current_user),
):
    notification = notifications_collection.find_one(
        {"_id": _parse_object_id(notification_id, "notification_id")}
    )

    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    if notification["user_id"] != str(current_user["_id"]):
        raise HTTPException(
            status_code=403, detail="You can only view your own notifications"
        )

    notification["_id"] = str(notification["_id"])
    return notification


@router.put("/{notification_id}/read", tags=["Notifications"])
def mark_as_read(
    notification_id: str,
    current_user: dict = Depends(get_current_user),
):
    notification = notifications_collection.find_one(
        {"_id": _parse_object_id(notification_id, "notification_id")}
    )

    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    if notification["user_id"] != str(current_user["_id"]):
        raise HTTPException(
            status_code=403, detail="You can only mark your own notifications"
        )

    notifications_collection.update_one(
        {"_id": _parse_object_id(notification_id, "notification_id")},
        {"$set": {"is_read": True, "read_at": datetime.now(timezone.utc)}},
    )

    return {"message": "Notification marked as read"}


@router.put("/mark-all-read", tags=["Notifications"])
def mark_all_as_read(current_user: dict = Depends(get_current_user)):
    notifications_collection.update_many(
        {"user_id": str(current_user["_id"]), "is_read": False},
        {"$set": {"is_read": True, "read_at": datetime.now(timezone.utc)}},
    )

    return {"message": "All notifications marked as read"}
