from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import get_current_user
from app.api.permissions import parse_object_id, require_ticket_membership
from app.db.db import (
    board_members_collection,
    comments_collection,
    notifications_collection,
    ticket_collection,
)
from app.models.comment import Comment
from app.models.notifications import Notifications
from app.models.project_member import ProjectRole

router = APIRouter()


@router.post("/comment/{ticket_id}", tags=["Comments"])
def create_comment(
    ticket_id: str, comment: Comment, current_user: dict = Depends(get_current_user)
):
    ticket = ticket_collection.find_one(
        {"_id": parse_object_id(ticket_id, "ticket_id")}
    )
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    membership = require_ticket_membership(
        ticket,
        current_user,
        board_members_collection,
    )
    if (
        membership.get("role") == ProjectRole.REPORTER.value
        and comment.visibility == "internal"
    ):
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
    result = comments_collection.insert_one(new_comment)
    notification = Notifications(
        ticket_id=ticket_id,
        comment_id=str(result.inserted_id),
        user_id=str(current_user["_id"]),
        event_type="comment_created",
    )
    notifications_collection.insert_one(notification.model_dump())

    return {"message": "Comment added"}


@router.get("/comments/{ticket_id}", tags=["Comments"])
def get_comments(ticket_id: str, current_user: dict = Depends(get_current_user)):
    ticket = ticket_collection.find_one(
        {"_id": parse_object_id(ticket_id, "ticket_id")}
    )
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    membership = require_ticket_membership(
        ticket,
        current_user,
        board_members_collection,
    )
    if membership.get("role") == ProjectRole.REPORTER.value:
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
