from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from app.api.permissions import (
    SUPER_ADMIN_ROLES,
    ensure_role,
    require_role,
)
from app.db.db import projects_collection, statuses_collection
from app.models.status import StatusCreate, StatusUpdate

router = APIRouter(prefix="/statuses", tags=["Statuses"])

_require_super_admin = require_role(
    *SUPER_ADMIN_ROLES,
    detail="Only super_admin can manage statuses",
)

DEFAULT_WORKFLOW_STATUSES = [
    {"name": "To Do", "order": 10},
    {"name": "In Development", "order": 20},
    {"name": "Code Review", "order": 30},
    {"name": "Ready for QA", "order": 40},
    {"name": "In QA", "order": 50},
    {"name": "QA Failed", "order": 60},
    {"name": "QA Passed", "order": 70},
    {"name": "Ready for Deployment", "order": 80},
    {"name": "Deployed", "order": 90},
    {"name": "Closed", "order": 100},
    {"name": "Reopened", "order": 110},
]


def _normalize_status_name(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def _serialize_status(status: dict) -> dict:
    status["_id"] = str(status["_id"])
    return status


@router.post("/")
def create_status(
    payload: StatusCreate,
    current_user: dict = Depends(_require_super_admin),
):
    ensure_role(
        current_user,
        SUPER_ADMIN_ROLES,
        detail="Only super_admin can manage statuses",
    )

    if statuses_collection.find_one({"name": payload.name}):
        raise HTTPException(
            status_code=400, detail=f"Status '{payload.name}' already exists"
        )

    if statuses_collection.find_one({"order": payload.order}):
        raise HTTPException(
            status_code=400,
            detail=f"A status with order {payload.order} already exists",
        )

    status = {
        "name": payload.name,
        "order": payload.order,
        "created_at": datetime.now(timezone.utc),
    }
    result = statuses_collection.insert_one(status)
    status["_id"] = str(result.inserted_id)
    return status


@router.post("/default-workflow")
def create_default_workflow_statuses(
    current_user: dict = Depends(_require_super_admin),
):
    ensure_role(
        current_user,
        SUPER_ADMIN_ROLES,
        detail="Only super_admin can manage statuses",
    )

    existing_statuses = list(statuses_collection.find())
    existing_by_name = {
        _normalize_status_name(status["name"]): status for status in existing_statuses
    }
    used_orders = {status.get("order") for status in existing_statuses}
    next_order = max([0, *[order for order in used_orders if order is not None]]) + 10

    workflow_statuses = []
    created_statuses = []
    now = datetime.now(timezone.utc)

    for default_status in DEFAULT_WORKFLOW_STATUSES:
        name_key = _normalize_status_name(default_status["name"])
        existing_status = existing_by_name.get(name_key)

        if existing_status:
            workflow_statuses.append(_serialize_status(existing_status))
            continue

        order = default_status["order"]
        if order in used_orders:
            order = next_order
            next_order += 10

        status = {
            "name": default_status["name"],
            "order": order,
            "created_at": now,
        }
        result = statuses_collection.insert_one(status)
        status["_id"] = result.inserted_id
        used_orders.add(order)
        created_statuses.append(_serialize_status(status.copy()))
        workflow_statuses.append(_serialize_status(status))

    workflow_statuses.sort(key=lambda status: status["order"])

    return {
        "message": "Default workflow statuses are ready",
        "created_count": len(created_statuses),
        "created_statuses": created_statuses,
        "workflow_statuses": workflow_statuses,
    }


@router.get("/")
def list_statuses(current_user: dict = Depends(_require_super_admin)):
    ensure_role(
        current_user,
        SUPER_ADMIN_ROLES,
        detail="Only super_admin can manage statuses",
    )
    statuses = list(statuses_collection.find().sort("order", 1))
    for s in statuses:
        s["_id"] = str(s["_id"])
    return statuses


@router.patch("/{status_id}")
def update_status(
    status_id: str,
    payload: StatusUpdate,
    current_user: dict = Depends(_require_super_admin),
):
    ensure_role(
        current_user,
        SUPER_ADMIN_ROLES,
        detail="Only super_admin can manage statuses",
    )

    status = statuses_collection.find_one({"_id": ObjectId(status_id)})
    if not status:
        raise HTTPException(status_code=404, detail="Status not found")

    update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields provided to update")

    statuses_collection.update_one({"_id": ObjectId(status_id)}, {"$set": update_data})
    return {"message": "Status updated successfully"}


@router.delete("/{status_id}")
def delete_status(
    status_id: str,
    current_user: dict = Depends(_require_super_admin),
):
    ensure_role(
        current_user,
        SUPER_ADMIN_ROLES,
        detail="Only super_admin can manage statuses",
    )

    status = statuses_collection.find_one({"_id": ObjectId(status_id)})
    if not status:
        raise HTTPException(status_code=404, detail="Status not found")

    project_using = projects_collection.find_one({"assigned_statuses": status_id})
    if project_using:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete — this status is assigned to one or more projects",
        )

    statuses_collection.delete_one({"_id": ObjectId(status_id)})
    return {"message": "Status deleted successfully"}
