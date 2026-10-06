# now my project has been completed, i hope it is a good learning project

from datetime import datetime, timedelta, timezone

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.api.auth import get_current_user
from app.api.permissions import (
    Capability,
    get_accessible_project_ids,
    parse_object_id,
    require_project_membership,
    require_ticket_membership,
)
from app.db.db import (
    project_activity_collection,
    project_members_collection,
    projects_collection,
    ticket_activity_collection,
    ticket_collection,
    user_collection,
)

router = APIRouter(prefix="/activities", tags=["Activity"])


def _period_start(period: str, now: datetime) -> datetime | None:
    if period == "week":
        return now - timedelta(days=7)
    if period == "month":
        return now - timedelta(days=30)
    return None


@router.get("/dashboard")
def get_dashboard_activity(
    period: str = "week",
    current_user=Depends(get_current_user),
):
    """Return status counts and recent persisted activity for the workspace."""
    if period not in {"week", "month", "all"}:
        raise HTTPException(status_code=400, detail="Invalid activity period")

    project_ids = get_accessible_project_ids(
        current_user,
        project_members_collection,
        projects_collection,
    )
    ticket_query = {"project_id": {"$in": project_ids}}
    now = datetime.now(timezone.utc)
    start = _period_start(period, now)
    if start:
        ticket_query["$or"] = [
            {"updated_at": {"$gte": start}},
            {"created_date": {"$gte": start}},
        ]

    tickets = list(ticket_collection.find(ticket_query))
    project_objects = []
    for project_id in project_ids:
        try:
            project_objects.append(ObjectId(project_id))
        except InvalidId:
            continue
    projects = {
        str(project["_id"]): project.get("code", "")
        for project in projects_collection.find({"_id": {"$in": project_objects}})
    }
    status_counts: dict[str, int] = {}
    ticket_ids = []
    ticket_lookup = {}
    for ticket in tickets:
        ticket_id = str(ticket["_id"])
        ticket_ids.append(ticket_id)
        ticket_lookup[ticket_id] = ticket
        status_name = str(ticket.get("current_status_name") or "To Do")
        status_counts[status_name] = status_counts.get(status_name, 0) + 1

    activity_query = {"ticket_id": {"$in": ticket_ids}}
    if start:
        activity_query["changed_at"] = {"$gte": start}
    activity_events = list(
        ticket_activity_collection.find(activity_query).sort("changed_at", -1).limit(10)
    )
    activities = []
    for event in activity_events:
        ticket = ticket_lookup.get(str(event.get("ticket_id")))
        if not ticket:
            continue
        changed_by = str(event.get("changed_by") or "")
        try:
            actor = user_collection.find_one({"_id": ObjectId(changed_by)})
        except InvalidId:
            actor = None
        activity = {
            "id": str(event["_id"]),
            "action": event.get("action", "updated"),
            "changed_by": changed_by,
            "actor_name": (
                actor.get("name", "Workspace member") if actor else "Workspace member"
            ),
            "changed_at": event.get("changed_at"),
            "ticket_code": ticket.get("ticket_code"),
            "ticket_title": ticket.get("title"),
            "project_key": projects.get(str(ticket.get("project_id")), ""),
        }
        activities.append(activity)

    return JSONResponse(
        content=jsonable_encoder(
            {
                "period": period,
                "total": len(tickets),
                "status_counts": status_counts,
                "activities": activities,
            }
        )
    )


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


def log_project_activity(
    *,
    project_id: str,
    action: str,
    changed_by: str,
    changes: list,
):
    """Persist an audit event for project administration changes."""
    activity = {
        "project_id": str(project_id),
        "action": action,
        "changed_by": str(changed_by),
        "changed_at": datetime.now(timezone.utc),
        "changes": changes,
    }
    project_activity_collection.insert_one(activity)


@router.get("/tickets/{ticket_id}")
def get_ticket_activity(ticket_id: str, current_user=Depends(get_current_user)):
    ticket_object_id = parse_object_id(ticket_id, "ticket_id")
    ticket = ticket_collection.find_one({"_id": ticket_object_id})
    if not ticket or ticket.get("deleted_at") is not None:
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


@router.get("/projects/{project_id}")
def get_project_activity(project_id: str, current_user=Depends(get_current_user)):
    project_object_id = parse_object_id(project_id, "project_id")
    project = projects_collection.find_one({"_id": project_object_id})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    require_project_membership(
        project_object_id,
        current_user,
        project_members_collection,
        allow_super_admin=True,
        capability=Capability.VIEW_PROJECT,
    )

    activities = list(
        project_activity_collection.find({"project_id": str(project_object_id)}).sort(
            "changed_at", -1
        )
    )
    return JSONResponse(
        content=jsonable_encoder(
            {
                "project_id": str(project_object_id),
                "activities": activities,
            }
        )
    )
