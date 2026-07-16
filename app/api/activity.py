from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.api.auth import get_current_user
from app.api.permissions import parse_object_id, require_ticket_membership
from app.db.db import (
    project_members_collection,
    ticket_activity_collection,
    ticket_collection,
)

router = APIRouter(prefix="/activities", tags=["Activity"])


def log_activity(
    *,
    ticket_id: str,
    action: str,
    changed_by: str,
    changes: list,
):
    activity = {
        "ticket_id": ticket_id,
        "action": action,
        "changed_by": str(changed_by),
        "changed_at": datetime.now(timezone.utc),
        "changes": changes,
    }

    ticket_activity_collection.insert_one(activity)


@router.get("/tickets/{ticket_id}")
def get_ticket_activity(ticket_id: str, current_user=Depends(get_current_user)):
    ticket_object_id = parse_object_id(ticket_id, "ticket_id")
    ticket = ticket_collection.find_one({"_id": ticket_object_id})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    require_ticket_membership(
        ticket,
        current_user,
        project_members_collection,
    )

    activities = list(
        ticket_activity_collection.find(
            {"ticket_id": {"$in": [ticket_id, ticket_object_id]}}
        ).sort("changed_at", -1)
    )

    for activity in activities:
        for key, value in activity.items():
            if isinstance(value, ObjectId):
                activity[key] = str(value)
            elif hasattr(value, "isoformat"):
                activity[key] = value.isoformat()

    payload = {
        "ticket_id": ticket_id,
        "ticket_code": ticket["ticket_code"],
        "activities": activities,
    }

    return JSONResponse(content=jsonable_encoder(payload))
