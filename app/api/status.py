from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import get_current_user
from app.db.db import boards_collection, statuses_collection
from app.models.status import StatusCreate, StatusUpdate

router = APIRouter(prefix="/statuses", tags=["Statuses"])


@router.post("/")
def create_status(
    payload: StatusCreate,
    current_user: dict = Depends(get_current_user),
):
    if current_user["role"] != "super_admin":
        raise HTTPException(
            status_code=403, detail="Only super_admin can create statuses"
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


@router.get("/")
def list_statuses(current_user: dict = Depends(get_current_user)):
    statuses = list(statuses_collection.find().sort("order", 1))
    for s in statuses:
        s["_id"] = str(s["_id"])
    return statuses


@router.patch("/{status_id}")
def update_status(
    status_id: str,
    payload: StatusUpdate,
    current_user: dict = Depends(get_current_user),
):
    if current_user["role"] != "super_admin":
        raise HTTPException(
            status_code=403, detail="Only super_admin can update statuses"
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
    current_user: dict = Depends(get_current_user),
):
    if current_user["role"] != "super_admin":
        raise HTTPException(
            status_code=403, detail="Only super_admin can delete statuses"
        )

    status = statuses_collection.find_one({"_id": ObjectId(status_id)})
    if not status:
        raise HTTPException(status_code=404, detail="Status not found")

    board_using = boards_collection.find_one({"assigned_statuses": status_id})
    if board_using:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete — this status is assigned to one or more boards",
        )

    statuses_collection.delete_one({"_id": ObjectId(status_id)})
    return {"message": "Status deleted successfully"}
