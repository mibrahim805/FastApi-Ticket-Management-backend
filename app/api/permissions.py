from collections.abc import Collection

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException

from app.models.project_member import ProjectRole

BOARD_MEMBER_ROLES = frozenset(role.value for role in ProjectRole)
BOARD_WORK_ROLES = frozenset(
    {
        ProjectRole.LEAD.value,
        ProjectRole.MEMBER.value,
    }
)
BOARD_LEAD_ROLES = frozenset({ProjectRole.LEAD.value})


def parse_object_id(value: str | ObjectId, field_name: str) -> ObjectId:
    if isinstance(value, ObjectId):
        return value

    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}")


def get_board_membership(
    board_id: str | ObjectId,
    user_id: str | ObjectId,
    board_members_collection,
) -> dict | None:
    return board_members_collection.find_one(
        {
            "board_id": parse_object_id(board_id, "board_id"),
            "user_id": parse_object_id(user_id, "user_id"),
        }
    )


def require_board_membership(
    board_id: str | ObjectId,
    current_user: dict,
    board_members_collection,
    *,
    allowed_roles: Collection[str] | None = None,
    role_detail: str = "You do not have permission to perform this board action",
) -> dict:
    user_id = current_user.get("_id")
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="Authenticated user is missing an ID",
        )

    membership = get_board_membership(
        board_id,
        user_id,
        board_members_collection,
    )
    if not membership:
        raise HTTPException(
            status_code=403,
            detail="You are not a member of this board",
        )

    if membership.get("role") not in BOARD_MEMBER_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Your board membership has an invalid role",
        )

    if allowed_roles is not None and membership.get("role") not in allowed_roles:
        raise HTTPException(status_code=403, detail=role_detail)

    return membership


def require_ticket_membership(
    ticket: dict,
    current_user: dict,
    board_members_collection,
    *,
    allowed_roles: Collection[str] | None = None,
    role_detail: str = "You do not have permission to perform this ticket action",
) -> dict:
    board_id = ticket.get("board_id")
    if not board_id:
        raise HTTPException(status_code=400, detail="Ticket is missing board_id")

    return require_board_membership(
        board_id,
        current_user,
        board_members_collection,
        allowed_roles=allowed_roles,
        role_detail=role_detail,
    )
