from datetime import datetime
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.auth import get_current_user
from app.db.db import notifications_collection

router = APIRouter(prefix="/notifications", tags=["Notifications"])


def _parse_object_id(value: str, field_name: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}")


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

    print("Query:", query)
    print("Collection:", notifications_collection)
    print("Documents:", notifications_collection.documents)
    print("Count:", notifications_collection.count_documents(query))

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
        {"$set": {"is_read": True, "read_at": datetime.utcnow()}},
    )

    return {"message": "Notification marked as read"}


@router.put("/mark-all-read", tags=["Notifications"])
def mark_all_as_read(current_user: dict = Depends(get_current_user)):
    notifications_collection.update_many(
        {"user_id": str(current_user["_id"]), "is_read": False},
        {"$set": {"is_read": True, "read_at": datetime.utcnow()}},
    )

    return {"message": "All notifications marked as read"}
