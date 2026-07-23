from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pymongo.errors import DuplicateKeyError

from app.api.auth import get_current_user
from app.api.permissions import parse_object_id
from app.db.db import saved_filters_collection
from app.models.saved_filter import SavedFilterCreate

router = APIRouter(tags=["Filters"])


def _serialize_saved_filter(saved_filter: dict) -> dict:
    serialized = dict(saved_filter)
    serialized["filter_id"] = str(serialized.pop("_id"))
    serialized.pop("name_key", None)
    return serialized


@router.get("/filters")
def list_saved_filters(current_user: dict = Depends(get_current_user)):
    saved_filters = saved_filters_collection.find(
        {"user_id": str(current_user["_id"])}
    ).sort("created_at", -1)
    return {
        "filters": [
            _serialize_saved_filter(saved_filter) for saved_filter in saved_filters
        ]
    }


@router.post("/filters", status_code=status.HTTP_201_CREATED)
def create_saved_filter(
    payload: SavedFilterCreate,
    current_user: dict = Depends(get_current_user),
):
    user_id = str(current_user["_id"])
    name_key = payload.name.casefold()
    if saved_filters_collection.find_one({"user_id": user_id, "name_key": name_key}):
        raise HTTPException(
            status_code=409,
            detail="You already have a saved filter with this name",
        )

    now = datetime.now(timezone.utc)
    saved_filter = {
        "user_id": user_id,
        "name": payload.name,
        "name_key": name_key,
        "query_params": payload.query_params,
        "created_at": now,
        "updated_at": now,
    }
    try:
        result = saved_filters_collection.insert_one(saved_filter)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="You already have a saved filter with this name",
        )

    saved_filter["_id"] = result.inserted_id
    return _serialize_saved_filter(saved_filter)


@router.delete("/filters/{filter_id}")
def delete_saved_filter(
    filter_id: str,
    current_user: dict = Depends(get_current_user),
):
    filter_object_id = parse_object_id(filter_id, "filter_id")
    saved_filter = saved_filters_collection.find_one(
        {
            "_id": filter_object_id,
            "user_id": str(current_user["_id"]),
        }
    )
    if not saved_filter:
        raise HTTPException(status_code=404, detail="Saved filter not found")

    saved_filters_collection.delete_one({"_id": filter_object_id})
    return {"message": "Saved filter deleted successfully"}
