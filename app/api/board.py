from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import get_current_user
from app.api.permissions import (
    BOARD_LEAD_ROLES,
    BOARD_MEMBER_ROLES,
    BOARD_WORK_ROLES,
    get_board_membership,
    parse_object_id,
    require_board_membership,
)
from app.db.db import (
    board_members_collection,
    boards_collection,
    statuses_collection,
    ticket_collection,
    user_collection,
)
from app.decorator.super_admin import super_admin_required
from app.models.board import (
    AssignBoard,
    BoardCreate,
    BoardMemberCreate,
    BoardRole,
    BoardStatusAssign,
)

router = APIRouter(tags=["Boards"])


@router.post("/boards")
@super_admin_required
def create_board(board: BoardCreate, current_user: dict = Depends(get_current_user)):
    current_user_id = parse_object_id(current_user.get("_id"), "user_id")
    result = boards_collection.insert_one(board.model_dump())

    board_members_collection.insert_one(
        {
            "board_id": result.inserted_id,
            "user_id": current_user_id,
            "role": BoardRole.LEAD.value,
        }
    )

    return {"id": str(result.inserted_id), "message": "Board created"}


@router.get("/boards")
def get_boards(current_user: dict = Depends(get_current_user)):
    user_id = parse_object_id(current_user.get("_id"), "user_id")
    memberships = list(board_members_collection.find({"user_id": user_id}))

    boards = []
    seen_board_ids = set()
    for membership in memberships:
        if membership.get("role") not in BOARD_MEMBER_ROLES:
            continue
        board_id = membership.get("board_id")
        if not board_id or board_id in seen_board_ids:
            continue
        board = boards_collection.find_one({"_id": board_id})
        if board:
            boards.append(board)
            seen_board_ids.add(board_id)

    for board in boards:
        board["_id"] = str(board["_id"])
        ticket_count = ticket_collection.count_documents({"board_id": board["_id"]})
        board["total_tickets"] = ticket_count
    return {"boards": boards}


@router.get("/boards/{board_id}")
def get_board(board_id: str, current_user: dict = Depends(get_current_user)):
    board_object_id = parse_object_id(board_id, "board_id")
    board = boards_collection.find_one({"_id": board_object_id})
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")

    require_board_membership(
        board_object_id,
        current_user,
        board_members_collection,
    )

    board["_id"] = str(board["_id"])
    return {"board": board}


@router.put("/boards/{board_id}")
def update_board(
    data: BoardCreate, board_id: str, current_user: dict = Depends(get_current_user)
):
    board_object_id = parse_object_id(board_id, "board_id")
    board = boards_collection.find_one({"_id": board_object_id})
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")

    require_board_membership(
        board_object_id,
        current_user,
        board_members_collection,
        allowed_roles=BOARD_LEAD_ROLES,
        role_detail="Only board leads can update the board",
    )

    boards_collection.update_one({"_id": board_object_id}, {"$set": data.model_dump()})
    return {"message": "Board updated"}


@router.put("/board/{board_id}/assign", tags=["Tickets"])
def assign_board(
    board_id: str, data: AssignBoard, current_user: dict = Depends(get_current_user)
):
    board_object_id = parse_object_id(board_id, "board_id")
    board = boards_collection.find_one({"_id": board_object_id})
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")

    require_board_membership(
        board_object_id,
        current_user,
        board_members_collection,
        allowed_roles=BOARD_LEAD_ROLES,
        role_detail="Only board leads can assign the board",
    )

    target_membership = get_board_membership(
        board_object_id,
        data.agent_id,
        board_members_collection,
    )
    if not target_membership or target_membership.get("role") not in BOARD_WORK_ROLES:
        raise HTTPException(
            status_code=400,
            detail="Target user must be a lead or member of this board",
        )

    boards_collection.update_one(
        {"_id": board_object_id},
        {
            "$set": {
                "assigned_agent": data.agent_id,
                "updated_by": str(current_user["_id"]),
            }
        },
    )
    return {"message": "Board assigned to agent"}


@router.delete("/boards/{board_id}")
def delete_board(board_id: str, current_user: dict = Depends(get_current_user)):
    board_object_id = parse_object_id(board_id, "board_id")
    board = boards_collection.find_one({"_id": board_object_id})
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")

    require_board_membership(
        board_object_id,
        current_user,
        board_members_collection,
        allowed_roles=BOARD_LEAD_ROLES,
        role_detail="Only board leads can delete the board",
    )

    boards_collection.delete_one({"_id": board_object_id})
    board_members_collection.delete_many({"board_id": board_object_id})
    return {"message": "Board deleted"}


@router.patch("/{board_id}/statuses", tags=["Boards"])
def assign_statuses_to_board(
    board_id: str,
    payload: BoardStatusAssign,
    current_user: dict = Depends(get_current_user),
):
    board_object_id = parse_object_id(board_id, "board_id")
    board = boards_collection.find_one({"_id": board_object_id})
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")

    require_board_membership(
        board_object_id,
        current_user,
        board_members_collection,
        allowed_roles=BOARD_LEAD_ROLES,
        role_detail="Only board leads can assign statuses",
    )

    if len(payload.status_ids) < 2:
        raise HTTPException(
            status_code=400, detail="Board must have at least 2 statuses"
        )

    if len(payload.status_ids) != len(set(payload.status_ids)):
        raise HTTPException(
            status_code=400, detail="Duplicate status IDs are not allowed"
        )

    statuses = []
    for sid in payload.status_ids:
        status = statuses_collection.find_one(
            {"_id": parse_object_id(sid, "status_id")}
        )
        if not status:
            raise HTTPException(status_code=404, detail=f"Status '{sid}' not found")
        statuses.append(status)

    statuses.sort(key=lambda s: s["order"])
    ordered_ids = [str(s["_id"]) for s in statuses]

    boards_collection.update_one(
        {"_id": board_object_id},
        {
            "$set": {
                "assigned_statuses": ordered_ids,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )

    return {
        "message": "Statuses assigned to board successfully",
        "board_id": board_id,
        "assigned_statuses": [
            {"_id": str(s["_id"]), "name": s["name"], "order": s["order"]}
            for s in statuses
        ],
        "first_status": {"_id": str(statuses[0]["_id"]), "name": statuses[0]["name"]},
        "last_status": {"_id": str(statuses[-1]["_id"]), "name": statuses[-1]["name"]},
    }


@router.post("/boards/{board_id}/members")
def add_board_member(
    board_id: str,
    member: BoardMemberCreate,
    current_user: dict = Depends(get_current_user),
):
    board_object_id = parse_object_id(board_id, "board_id")
    board = boards_collection.find_one({"_id": board_object_id})
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")

    require_board_membership(
        board_object_id,
        current_user,
        board_members_collection,
        allowed_roles=BOARD_LEAD_ROLES,
        role_detail="Only board leads can add members",
    )

    member_user_id = parse_object_id(member.user_id, "user_id")
    user = user_collection.find_one({"_id": member_user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    existing_member = board_members_collection.find_one(
        {
            "board_id": board_object_id,
            "user_id": member_user_id,
        }
    )

    if existing_member:
        raise HTTPException(
            status_code=409,
            detail="User is already a member of this board",
        )

    result = board_members_collection.insert_one(
        {
            "board_id": board_object_id,
            "user_id": member_user_id,
            "role": member.role.value,
        }
    )

    return {
        "id": str(result.inserted_id),
        "message": "Board member added successfully",
    }


@router.get("/boards/{board_id}/members")
def get_board_members(
    board_id: str,
    current_user: dict = Depends(get_current_user),
):
    board_object_id = parse_object_id(board_id, "board_id")
    board = boards_collection.find_one({"_id": board_object_id})
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")

    require_board_membership(
        board_object_id,
        current_user,
        board_members_collection,
    )

    members = []

    for member in board_members_collection.find({"board_id": board_object_id}):
        user = user_collection.find_one(
            {"_id": member["user_id"]},
            {
                "name": 1,
                "email": 1,
            },
        )

        members.append(
            {
                "user_id": str(member["user_id"]),
                "name": user.get("name"),
                "email": user.get("email"),
                "role": member["role"],
            }
        )

    return members
