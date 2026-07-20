import re
from collections.abc import Iterable
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from app.api.activity import log_activity
from app.api.auth import get_current_user
from app.api.permissions import (
    get_project_membership,
    parse_object_id,
    require_ticket_participant,
)
from app.api.watcher_notifications import notify_ticket_watchers
from app.db.db import (
    comments_collection,
    notifications_collection,
    project_members_collection,
    ticket_collection,
    user_collection,
)
from app.models.comment import (
    Comment,
    CommentReply,
    CommentUpdate,
    CommentVisibility,
)
from app.models.notifications import Notifications
from app.models.project_member import ProjectRole

router = APIRouter()

MENTION_EVENT_TYPE = "comment_mentioned"
MENTION_PATTERN = re.compile(
    r"(?<![\w.+-])@([A-Za-z0-9_](?:[A-Za-z0-9_.-]*[A-Za-z0-9_])?)"
)


def _visibility_value(visibility: CommentVisibility | str) -> str:
    if isinstance(visibility, CommentVisibility):
        return visibility.value
    return visibility


def _get_ticket_or_404(ticket_id: str) -> tuple[str, dict]:
    ticket_object_id = parse_object_id(ticket_id, "ticket_id")
    ticket = ticket_collection.find_one({"_id": ticket_object_id})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return str(ticket_object_id), ticket


def _get_comment_or_404(comment_id: str) -> tuple[ObjectId, dict]:
    comment_object_id = parse_object_id(comment_id, "comment_id")
    comment = comments_collection.find_one({"_id": comment_object_id})
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    return comment_object_id, comment


def _require_comment_visible(comment: dict, membership: dict) -> None:
    if (
        membership.get("role") == ProjectRole.REPORTER.value
        and comment.get("visibility") == CommentVisibility.INTERNAL.value
    ):
        # Do not disclose the existence of an internal comment to reporters.
        raise HTTPException(status_code=404, detail="Comment not found")


def _require_comment_author(comment: dict, current_user: dict) -> None:
    is_author = str(comment.get("author_id")) == str(current_user.get("_id"))
    if not is_author and current_user.get("role") != "super_admin":
        raise HTTPException(
            status_code=403,
            detail="Only the comment author can modify this comment",
        )


def _serialize_comment(comment: dict) -> dict:
    serialized = dict(comment)
    if isinstance(serialized.get("_id"), ObjectId):
        serialized["_id"] = str(serialized["_id"])
    if isinstance(serialized.get("parent_id"), ObjectId):
        serialized["parent_id"] = str(serialized["parent_id"])
    serialized.setdefault("parent_id", None)
    serialized.setdefault("mentioned_user_ids", [])
    return serialized


def _build_comment_threads(comments: list[dict]) -> list[dict]:
    by_id = {}
    for comment in comments:
        threaded_comment = dict(comment)
        threaded_comment["replies"] = []
        by_id[str(threaded_comment["_id"])] = threaded_comment

    roots = []
    for comment in by_id.values():
        parent = by_id.get(str(comment.get("parent_id")))
        if parent is None:
            roots.append(comment)
        else:
            parent["replies"].append(comment)
    return roots


def _mention_handles(message: str) -> list[str]:
    return list(
        dict.fromkeys(
            match.group(1).casefold() for match in MENTION_PATTERN.finditer(message)
        )
    )


def _find_user_by_handle(handle: str) -> dict | None:
    exact_handle = re.compile(rf"^{re.escape(handle)}$", re.IGNORECASE)
    user = user_collection.find_one({"username": {"$regex": exact_handle}})
    if user:
        return user

    user = user_collection.find_one({"name": {"$regex": exact_handle}})
    if user:
        return user

    email_local_part = re.compile(rf"^{re.escape(handle)}@", re.IGNORECASE)
    return user_collection.find_one({"email": {"$regex": email_local_part}})


def _require_mentioned_user_access(
    *,
    handle: str,
    user: dict,
    ticket: dict,
    visibility: str,
) -> None:
    membership = get_project_membership(
        ticket["project_id"],
        user["_id"],
        project_members_collection,
    )
    if not membership:
        raise HTTPException(
            status_code=400,
            detail=(f"Mentioned user '@{handle}' is not a member of this project"),
        )

    if (
        membership.get("role") == ProjectRole.REPORTER.value
        and visibility == CommentVisibility.INTERNAL.value
    ):
        raise HTTPException(
            status_code=400,
            detail=(f"Mentioned user '@{handle}' cannot view internal comments"),
        )


def _resolve_mentions(
    *,
    message: str,
    ticket: dict,
    visibility: str,
    actor_id,
) -> list[str]:
    actor_id = str(actor_id)
    mentioned_user_ids = []
    for handle in _mention_handles(message):
        user = _find_user_by_handle(handle)
        if not user:
            raise HTTPException(
                status_code=400,
                detail=f"Mentioned user '@{handle}' was not found",
            )

        user_id = str(user["_id"])
        if user_id == actor_id or user_id in mentioned_user_ids:
            continue

        if user.get("is_active") is False:
            raise HTTPException(
                status_code=400,
                detail=f"Mentioned user '@{handle}' is inactive",
            )

        _require_mentioned_user_access(
            handle=handle,
            user=user,
            ticket=ticket,
            visibility=visibility,
        )
        mentioned_user_ids.append(user_id)
    return mentioned_user_ids


def _notify_mentions(
    *,
    ticket_id: str,
    comment_id: str,
    actor_id,
    mentioned_user_ids: Iterable[str],
    previous_user_ids: Iterable[str] = (),
) -> int:
    actor_id = str(actor_id)
    previous_user_ids = set(map(str, previous_user_ids))
    notified = 0
    for user_id in mentioned_user_ids:
        if user_id in previous_user_ids:
            continue
        notifications_collection.insert_one(
            {
                "ticket_id": ticket_id,
                "comment_id": comment_id,
                "user_id": user_id,
                "actor_id": actor_id,
                "event_type": MENTION_EVENT_TYPE,
                "created_at": datetime.now(timezone.utc),
                "is_read": False,
            }
        )
        notified += 1
    return notified


def _validate_parent_comment(
    *,
    parent_id: str,
    ticket_id: str,
    visibility: str,
    membership: dict,
) -> dict:
    _, parent = _get_comment_or_404(parent_id)
    if str(parent.get("ticket_id")) != ticket_id:
        raise HTTPException(
            status_code=400,
            detail="Parent comment belongs to a different ticket",
        )
    _require_comment_visible(parent, membership)
    if parent.get("visibility") != visibility:
        raise HTTPException(
            status_code=400,
            detail="A reply must use the same visibility as its parent comment",
        )
    return parent


def _create_comment_document(
    *,
    ticket_id: str,
    ticket: dict,
    membership: dict,
    comment: Comment,
    current_user: dict,
    parent_id: str | None = None,
) -> dict:
    visibility = _visibility_value(comment.visibility)
    if (
        membership.get("role") == ProjectRole.REPORTER.value
        and visibility == CommentVisibility.INTERNAL.value
    ):
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to create internal comments",
        )

    canonical_parent_id = None
    if parent_id is not None:
        parent_object_id = parse_object_id(parent_id, "parent_id")
        canonical_parent_id = str(parent_object_id)
        _validate_parent_comment(
            parent_id=canonical_parent_id,
            ticket_id=ticket_id,
            visibility=visibility,
            membership=membership,
        )

    mentioned_user_ids = _resolve_mentions(
        message=comment.message,
        ticket=ticket,
        visibility=visibility,
        actor_id=current_user["_id"],
    )
    new_comment = {
        "ticket_id": ticket_id,
        "author_id": str(current_user["_id"]),
        "message": comment.message,
        "visibility": visibility,
        "parent_id": canonical_parent_id,
        "mentioned_user_ids": mentioned_user_ids,
        "created_at": datetime.now(timezone.utc),
    }
    result = comments_collection.insert_one(new_comment)
    comment_id = str(result.inserted_id)
    new_comment["_id"] = result.inserted_id

    log_activity(
        ticket_id=ticket_id,
        action="comment_created",
        changed_by=current_user["_id"],
        changes=[
            {
                "field": "comment",
                "old_value": None,
                "new_value": {
                    "comment_id": comment_id,
                    "parent_id": canonical_parent_id,
                    "visibility": visibility,
                },
            }
        ],
    )
    notification = Notifications(
        ticket_id=ticket_id,
        comment_id=comment_id,
        user_id=str(current_user["_id"]),
        event_type="comment_created",
    )
    notifications_collection.insert_one(notification.model_dump())
    _notify_mentions(
        ticket_id=ticket_id,
        comment_id=comment_id,
        actor_id=current_user["_id"],
        mentioned_user_ids=mentioned_user_ids,
    )
    notify_ticket_watchers(
        ticket=ticket,
        ticket_id=ticket_id,
        event_type="comment_created",
        actor_id=current_user["_id"],
        notifications_collection=notifications_collection,
        comment_id=comment_id,
    )
    return _serialize_comment(new_comment)


@router.post(
    "/tickets/{ticket_id}/comments",
    tags=["Comments"],
    status_code=201,
)
def create_comment(
    ticket_id: str,
    comment: Comment,
    current_user: dict = Depends(get_current_user),
):
    canonical_ticket_id, ticket = _get_ticket_or_404(ticket_id)
    membership = require_ticket_participant(
        ticket,
        current_user,
        project_members_collection,
    )
    created_comment = _create_comment_document(
        ticket_id=canonical_ticket_id,
        ticket=ticket,
        membership=membership,
        comment=comment,
        current_user=current_user,
    )
    return {
        "message": "Comment added",
        "comment_id": created_comment["_id"],
        "comment": created_comment,
    }


@router.post("/comments/{parent_comment_id}/replies", tags=["Comments"])
def create_reply(
    parent_comment_id: str,
    reply: CommentReply,
    current_user: dict = Depends(get_current_user),
):
    parent_object_id, parent = _get_comment_or_404(parent_comment_id)
    ticket_id, ticket = _get_ticket_or_404(str(parent.get("ticket_id")))
    membership = require_ticket_participant(
        ticket,
        current_user,
        project_members_collection,
    )
    _require_comment_visible(parent, membership)
    created_comment = _create_comment_document(
        ticket_id=ticket_id,
        ticket=ticket,
        membership=membership,
        comment=Comment(
            message=reply.message,
            visibility=parent["visibility"],
        ),
        current_user=current_user,
        parent_id=str(parent_object_id),
    )
    return {
        "message": "Reply added",
        "comment_id": created_comment["_id"],
        "comment": created_comment,
    }


@router.get("/tickets/{ticket_id}/comments", tags=["Comments"])
def get_comments(
    ticket_id: str,
    current_user: dict = Depends(get_current_user),
    threaded: bool = False,
):
    canonical_ticket_id, ticket = _get_ticket_or_404(ticket_id)
    membership = require_ticket_participant(
        ticket,
        current_user,
        project_members_collection,
    )
    comment_query = {"ticket_id": canonical_ticket_id}
    if membership.get("role") == ProjectRole.REPORTER.value:
        comment_query["visibility"] = CommentVisibility.PUBLIC.value

    comments = [
        _serialize_comment(comment)
        for comment in comments_collection.find(comment_query).sort("created_at", 1)
    ]
    if threaded:
        return _build_comment_threads(comments)
    return comments


@router.patch("/comments/{comment_id}", tags=["Comments"])
def update_comment(
    comment_id: str,
    update: CommentUpdate,
    current_user: dict = Depends(get_current_user),
):
    comment_object_id, existing_comment = _get_comment_or_404(comment_id)
    ticket_id, ticket = _get_ticket_or_404(str(existing_comment.get("ticket_id")))
    membership = require_ticket_participant(
        ticket,
        current_user,
        project_members_collection,
    )
    _require_comment_visible(existing_comment, membership)
    _require_comment_author(existing_comment, current_user)

    update_data = update.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No comment fields supplied")

    new_message = update_data.get("message", existing_comment["message"])
    new_visibility = _visibility_value(
        update_data.get("visibility", existing_comment["visibility"])
    )
    if (
        membership.get("role") == ProjectRole.REPORTER.value
        and new_visibility == CommentVisibility.INTERNAL.value
    ):
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to create internal comments",
        )

    parent_id = existing_comment.get("parent_id")
    if parent_id:
        _validate_parent_comment(
            parent_id=str(parent_id),
            ticket_id=ticket_id,
            visibility=new_visibility,
            membership=membership,
        )
    elif new_visibility != existing_comment.get(
        "visibility"
    ) and comments_collection.find_one({"parent_id": str(comment_object_id)}):
        raise HTTPException(
            status_code=409,
            detail="A comment with replies cannot change visibility",
        )

    mentioned_user_ids = _resolve_mentions(
        message=new_message,
        ticket=ticket,
        visibility=new_visibility,
        actor_id=current_user["_id"],
    )
    update_fields = {
        **update_data,
        "visibility": new_visibility,
        "mentioned_user_ids": mentioned_user_ids,
        "updated_at": datetime.now(timezone.utc),
        "updated_by": str(current_user["_id"]),
    }
    comments_collection.update_one(
        {"_id": comment_object_id},
        {"$set": update_fields},
    )

    _notify_mentions(
        ticket_id=ticket_id,
        comment_id=str(comment_object_id),
        actor_id=current_user["_id"],
        mentioned_user_ids=mentioned_user_ids,
        previous_user_ids=existing_comment.get("mentioned_user_ids", []),
    )
    notify_ticket_watchers(
        ticket=ticket,
        ticket_id=ticket_id,
        event_type="comment_updated",
        actor_id=current_user["_id"],
        notifications_collection=notifications_collection,
        comment_id=str(comment_object_id),
    )
    log_activity(
        ticket_id=ticket_id,
        action="comment_updated",
        changed_by=current_user["_id"],
        changes=[
            {
                "field": "comment",
                "old_value": {
                    "comment_id": str(comment_object_id),
                    "visibility": existing_comment.get("visibility"),
                },
                "new_value": {
                    "comment_id": str(comment_object_id),
                    "visibility": new_visibility,
                },
            }
        ],
    )
    return {"message": "Comment updated successfully"}


def _reparent_direct_replies(comment: dict) -> int:
    comment_id = str(comment["_id"])
    new_parent_id = comment.get("parent_id")
    direct_replies = list(
        comments_collection.find(
            {
                "ticket_id": str(comment["ticket_id"]),
                "parent_id": comment_id,
            }
        )
    )
    for reply in direct_replies:
        comments_collection.update_one(
            {"_id": reply["_id"]},
            {"$set": {"parent_id": new_parent_id}},
        )
    return len(direct_replies)


@router.delete("/comments/{comment_id}", tags=["Comments"])
def delete_comment(
    comment_id: str,
    current_user: dict = Depends(get_current_user),
):
    _, existing_comment = _get_comment_or_404(comment_id)
    ticket_id, ticket = _get_ticket_or_404(str(existing_comment.get("ticket_id")))
    membership = require_ticket_participant(
        ticket,
        current_user,
        project_members_collection,
    )
    _require_comment_visible(existing_comment, membership)
    _require_comment_author(existing_comment, current_user)

    reparented_reply_count = _reparent_direct_replies(existing_comment)
    notify_ticket_watchers(
        ticket=ticket,
        ticket_id=ticket_id,
        event_type="comment_deleted",
        actor_id=current_user["_id"],
        notifications_collection=notifications_collection,
        comment_id=comment_id,
    )
    log_activity(
        ticket_id=ticket_id,
        action="comment_deleted",
        changed_by=current_user["_id"],
        changes=[
            {
                "field": "comment",
                "old_value": {
                    "comment_id": comment_id,
                    "visibility": existing_comment.get("visibility"),
                },
                "new_value": None,
            }
        ],
    )
    comments_collection.delete_one({"_id": existing_comment["_id"]})
    return {
        "message": "Comment deleted successfully",
        "reparented_reply_count": reparented_reply_count,
    }
