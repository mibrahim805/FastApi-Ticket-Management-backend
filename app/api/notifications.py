from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo import ReturnDocument

from app.api.auth import get_current_user
from app.api.permissions import require_project_membership
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
    require_project_membership(
        project_object_id,
        current_user,
        project_members_collection,
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
