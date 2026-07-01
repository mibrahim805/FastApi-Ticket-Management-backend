from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.db.db import ticket_activity_collection, ticket_collection

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
def get_ticket_activity(ticket_id: str):
    ticket = ticket_collection.find_one({"_id": ObjectId(ticket_id)})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    activities = list(
        ticket_activity_collection.find(
            {"ticket_id": {"$in": [ticket_id, ObjectId(ticket_id)]}}
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

    print("PAYLOAD ABOUT TO RETURN:", payload)

    return JSONResponse(content=jsonable_encoder(payload))
