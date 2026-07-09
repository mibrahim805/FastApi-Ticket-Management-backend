from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import get_current_user
from app.db.db import comments_collection, notifications_collection, ticket_collection
from app.models.comment import Comment
from app.models.notifications import Notifications

router = APIRouter()


@router.post("/comment/{ticket_id}", tags=["Comments"])
def create_comment(
    ticket_id: str, comment: Comment, current_user: dict = Depends(get_current_user)
):
    ticket = ticket_collection.find_one({"_id": ObjectId(ticket_id)})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if (
        str(current_user["_id"]) != ticket["userId"]
        and current_user["role"] != "super_admin"
    ):
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to comment on this ticket",
        )
    if current_user["role"] == "customer" and comment.visibility == "internal":
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to create internal comments",
        )

    new_comment = {
        "ticket_id": ticket_id,
        "author_id": str(current_user["_id"]),
        "message": comment.message,
        "visibility": comment.visibility,
    }
    comments_collection.insert_one(new_comment)
    notification = Notifications(
        ticket_id=ticket_id,
        comment_id=str(new_comment["_id"]),
        user_id=str(current_user["_id"]),
        event_type="comment_created",
    )
    notifications_collection.insert_one(notification.model_dump())

    return {"message": "Comment added"}


@router.get("/comments/{ticket_id}", tags=["Comments"])
def get_comments(ticket_id: str, current_user: dict = Depends(get_current_user)):
    if current_user["role"] == "customer":
        comments = list(
            comments_collection.find(
                {"ticket_id": ticket_id, "visibility": "public"}
            ).sort("created_at", 1)
        )
        for comment in comments:
            comment["_id"] = str(comment["_id"])
        return comments
    comments = list(
        comments_collection.find({"ticket_id": ticket_id}).sort("created_at", 1)
    )
    for comment in comments:
        comment["_id"] = str(comment["_id"])
    return comments
