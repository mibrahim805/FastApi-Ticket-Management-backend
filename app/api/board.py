from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import get_current_user
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
    result = boards_collection.insert_one(board.model_dump())

    board_members_collection.insert_one(
        {
            "board_id": result.inserted_id,
            "user_id": current_user["_id"],
            "role": BoardRole.LEAD.value,
        }
    )

    return {"id": str(result.inserted_id), "message": "Board created"}


@router.get("/boards")
@super_admin_required
def get_boards(current_user: dict = Depends(get_current_user)):
    boards = list(boards_collection.find())
    for board in boards:
        board["_id"] = str(board["_id"])
        ticket_count = ticket_collection.count_documents({"board_id": board["_id"]})
        board["total_tickets"] = ticket_count
    return {"boards": boards}


@router.get("/boards/{board_id}")
# @super_admin_required
def get_board(board_id: str, current_user: dict = Depends(get_current_user)):
    board = boards_collection.find_one({"_id": ObjectId(board_id)})
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")
    if current_user["role"] != "super_admin" and board.get("assigned_agent") != str(
        current_user["_id"]
    ):
        return {"message": "You have no access to view the board"}
    board["_id"] = str(board["_id"])
    return {"board": board}


@router.put("/boards/{board_id}")
@super_admin_required
def update_board(
    data: BoardCreate, board_id: str, current_user: dict = Depends(get_current_user)
):
    board = boards_collection.find_one({"_id": ObjectId(board_id)})
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")
    boards_collection.update_one(
        {"_id": ObjectId(board_id)}, {"$set": data.model_dump()}
    )
    return {"message": "Board updated"}


@router.put("/board/{board_id}/assign", tags=["Tickets"])
@super_admin_required
def assign_board(
    board_id: str, data: AssignBoard, current_user: dict = Depends(get_current_user)
):
    board = boards_collection.find_one({"_id": ObjectId(board_id)})
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")

    if (
        board.get("assigned_agent")
        and board["assigned_agent"] != data.agent_id
        and current_user["role"] != "super_admin"
    ):
        raise HTTPException(
            status_code=403, detail="Only super admin can reassign tickets"
        )
    boards_collection.update_one(
        {"_id": ObjectId(board_id)},
        {
            "$set": {
                "assigned_agent": data.agent_id,
                "updated_by": str(current_user["_id"]),
            }
        },
    )
    return {"message": "Board assigned to agent"}


@router.delete("/boards/{board_id}")
@super_admin_required
def delete_board(board_id: str, current_user: dict = Depends(get_current_user)):
    board = boards_collection.find_one({"_id": ObjectId(board_id)})
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")
    boards_collection.delete_one({"_id": ObjectId(board_id)})
    return {"message": "Board deleted"}


@router.patch("/{board_id}/statuses", tags=["Boards"])
def assign_statuses_to_board(
    board_id: str,
    payload: BoardStatusAssign,
    current_user: dict = Depends(get_current_user),
):
    if current_user["role"] != "super_admin":
        raise HTTPException(
            status_code=403, detail="Only super_admin can assign statuses"
        )

    board = boards_collection.find_one({"_id": ObjectId(board_id)})
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")

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
        status = statuses_collection.find_one({"_id": ObjectId(sid)})
        if not status:
            raise HTTPException(status_code=404, detail=f"Status '{sid}' not found")
        statuses.append(status)

    statuses.sort(key=lambda s: s["order"])
    ordered_ids = [str(s["_id"]) for s in statuses]

    boards_collection.update_one(
        {"_id": ObjectId(board_id)},
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

    board = boards_collection.find_one({"_id": ObjectId(board_id)})
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")

    board_member = board_members_collection.find_one(
        {
            "board_id": ObjectId(board_id),
            "user_id": current_user["_id"],
        }
    )

    if not board_member:
        raise HTTPException(
            status_code=403,
            detail="You are not a member of this board",
        )

    if board_member["role"] != BoardRole.LEAD.value:
        raise HTTPException(
            status_code=403,
            detail="Only board leads can add members",
        )

    user = user_collection.find_one({"_id": ObjectId(member.user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    existing_member = board_members_collection.find_one(
        {
            "board_id": ObjectId(board_id),
            "user_id": ObjectId(member.user_id),
        }
    )

    if existing_member:
        raise HTTPException(
            status_code=409,
            detail="User is already a member of this board",
        )

    result = board_members_collection.insert_one(
        {
            "board_id": ObjectId(board_id),
            "user_id": ObjectId(member.user_id),
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

    board = boards_collection.find_one({"_id": ObjectId(board_id)})
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")

    membership = board_members_collection.find_one(
        {
            "board_id": ObjectId(board_id),
            "user_id": current_user["_id"],
        }
    )

    if not membership:
        raise HTTPException(
            status_code=403,
            detail="You are not a member of this board",
        )

    members = []

    for member in board_members_collection.find({"board_id": ObjectId(board_id)}):
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
