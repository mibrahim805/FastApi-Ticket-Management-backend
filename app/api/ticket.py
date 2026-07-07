import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.activity import log_activity
from app.api.auth import get_current_user
from app.db.db import (
    boards_collection,
    comments_collection,
    notifications_collection,
    statuses_collection,
    ticket_collection,
    user_collection,
)
from app.models.notifications import Notifications
from app.models.ticket import AssignAgent, TicketCreate, TicketUpdate

router = APIRouter()


def _parse_object_id(value: str, field_name: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}")


def calculate_due_date(priority: str) -> datetime:
    now = datetime.now(timezone.utc)

    sla_days = {
        "high": 1,
        "medium": 3,
        "low": 7,
    }

    return now + timedelta(days=sla_days[priority])


@router.post("/", tags=["Tickets"])
def create_ticket(
    payload: TicketCreate,
    current_user: dict = Depends(get_current_user),
):

    board = boards_collection.find_one(
        {"_id": _parse_object_id(payload.board_id, "board_id")}
    )
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")

    if current_user["role"] != "super_admin" and board["assigned_agent"] != str(
        current_user["_id"]
    ):
        raise HTTPException(
            status_code=403,
            detail="You can only create tickets for boards assigned to you",
        )

    assigned_statuses = board.get("assigned_statuses", [])
    if not assigned_statuses:
        raise HTTPException(
            status_code=400,
            detail="Board has no statuses assigned. Contact admin.",
        )

    first_status_id = assigned_statuses[0]
    first_status = statuses_collection.find_one(
        {"_id": _parse_object_id(first_status_id, "status_id")}
    )
    if not first_status:
        raise HTTPException(
            status_code=400,
            detail="Board's first status not found. Contact admin.",
        )

    next_number = board.get("ticket_counter", 0) + 1
    ticket_code = f"{board.get('code', 'BOARD')}-{next_number}"

    boards_collection.update_one(
        {"_id": _parse_object_id(payload.board_id, "board_id")},
        {"$set": {"ticket_counter": next_number}},
    )

    ticket = {
        "board_id": str(board["_id"]),
        "ticket_code": ticket_code,
        "board_code": board.get("code"),
        "userId": str(current_user["_id"]),
        "title": payload.title,
        "description": payload.description,
        "category": payload.category.value,
        "priority": payload.priority.value,
        "current_status_id": first_status_id,
        "current_status_name": first_status["name"],
        "assigned_agent": None,
        "created_date": datetime.now(timezone.utc),
        "due_date": calculate_due_date(payload.priority.value),
        "resolved_date": None,
        "closed_date": None,
    }

    result = ticket_collection.insert_one(ticket)
    ticket["_id"] = str(result.inserted_id)
    log_activity(
        ticket_id=ticket["_id"],
        action="ticket_created",
        changes=[],
        changed_by=current_user["_id"],
    )

    notification = Notifications(
        ticket_id=ticket["_id"],
        user_id=str(current_user["_id"]),
        event_type="ticket_created",
    )
    notification_record = notifications_collection.insert_one(notification.dict())
    ticket["notification_id"] = str(notification_record.inserted_id)

    return ticket


@router.get("/tickets/detail", tags=["Tickets"])
def get_ticket_details(current_user: dict = Depends(get_current_user), ticket_id=None):
    ticket = ticket_collection.find_one(
        {"_id": _parse_object_id(ticket_id, "ticket_id")}
    )
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if current_user["role"] != "super_admin" and ticket["userId"] != str(
        current_user["_id"]
    ):
        raise HTTPException(
            status_code=403, detail="You can only view your own tickets"
        )
    if ticket:
        ticket["_id"] = str(ticket["_id"])
    comments = list(comments_collection.find({"ticket_id": ticket_id}))
    for comment in comments:
        comment["_id"] = str(comment["_id"])

    return {
        "tickets": ticket,
        "comments": comments,
    }


@router.put("/update_ticket/{ticket_id}", tags=["Tickets"])
def update_ticket(
    ticket_id: str, data: TicketUpdate, current_user: dict = Depends(get_current_user)
):
    ticket = ticket_collection.find_one(
        {"_id": _parse_object_id(ticket_id, "ticket_id")}
    )
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if current_user["role"] != "super_admin" and ticket["userId"] != str(
        current_user["_id"]
    ):
        raise HTTPException(
            status_code=403, detail="You can only update your own tickets"
        )
    update_data = data.model_dump(exclude_none=True)
    update_data["updated_by"] = str(current_user["_id"])
    update_data["updated_at"] = datetime.utcnow()
    ticket_collection.update_one(
        {"_id": _parse_object_id(ticket_id, "ticket_id")}, {"$set": update_data}
    )

    notification = Notifications(
        ticket_id=ticket_id,
        user_id=str(current_user["_id"]),
        event_type="ticket_updated",
    )

    notifications_collection.insert_one(notification.model_dump())
    new_ticket = ticket_collection.find_one(
        {"_id": _parse_object_id(ticket_id, "ticket_id")}
    )
    TRACKED_FIELDS = [
        "title",
        "description",
        "priority",
        "status",
        "category",
        "assigned_to",
        "due_date",
    ]

    changes = []

    for field in TRACKED_FIELDS:
        if ticket.get(field) != new_ticket.get(field):
            changes.append(
                {
                    "field": field,
                    "old_value": ticket.get(field),
                    "new_value": new_ticket.get(field),
                }
            )
    if changes:
        log_activity(
            ticket_id=ticket_id,
            action="ticket_updated",
            changed_by=current_user["_id"],
            changes=changes,
        )
    return {"message": "Ticket updated successfully"}


@router.put("/tickets/{ticket_id}/assign", tags=["Tickets"])
def assign_ticket(
    ticket_id: str, data: AssignAgent, current_user: dict = Depends(get_current_user)
):
    ticket = ticket_collection.find_one(
        {"_id": _parse_object_id(ticket_id, "ticket_id")}
    )
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if current_user["role"] != "super_admin":
        raise HTTPException(
            status_code=403, detail="only super admin can assign ticket"
        )

    agent_object_id = _parse_object_id(data.agent_id, "agent_id")
    agent = user_collection.find_one({"_id": agent_object_id})
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.get("role") != "agent":
        raise HTTPException(status_code=400, detail="Target user is not an agent")
    if ticket.get("assigned_agent"):
        raise HTTPException(status_code=400, detail="Ticket is already assigned")
    ticket_collection.update_one(
        {"_id": _parse_object_id(ticket_id, "ticket_id")},
        {
            "$set": {
                "assigned_agent": data.agent_id,
                "updated_by": str(current_user["_id"]),
            }
        },
    )

    notification = Notifications(
        ticket_id=ticket_id,
        user_id=str(current_user["_id"]),
        event_type="ticket_assigned",
    )
    notifications_collection.insert_one(notification.dict())

    return {"message": "Ticket assigned successfully"}


@router.get("/tickets/{board_code}", tags=["Tickets"])
def get_tickets(
    board_code: str,
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(10, ge=1, le=100, description="Results per page"),
    status: Optional[str] = Query(
        None, description="Filter by status (e.g. open, closed, pending)"
    ),
    priority: Optional[str] = Query(
        None, description="Filter by priority (e.g. low, medium, high)"
    ),
    category: Optional[str] = Query(None, description="Filter by category"),
    assigned_agent: Optional[str] = Query(
        None, description="Filter by assigned agent ID"
    ),
    search: Optional[str] = Query(None, description="Search by title or ticket number"),
    sort_by: Optional[str] = Query(
        "created_at", regex="^(created_at|priority|due_date)$", description="Sort field"
    ),
    sort_order: Optional[str] = Query(
        "desc", regex="^(asc|desc)$", description="Sort direction"
    ),
    current_user: dict = Depends(get_current_user),
):
    query = {}
    board = boards_collection.find_one({"code": board_code})
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")

    query["board_code"] = board["code"]

    if current_user["role"] == "super_admin":
        pass
    else:
        query["userId"] = str(current_user["_id"])
    if status:
        query["status"] = status.lower()
    if priority:
        query["priority"] = priority.lower()
    if category:
        query["category"] = category
    if assigned_agent:
        query["assigned_agent"] = assigned_agent

    if search:
        search_regex = re.compile(re.escape(search), re.IGNORECASE)
        query["$or"] = [
            {"title": {"$regex": search_regex}},
            {"ticketNumber": {"$regex": search_regex}},
        ]

    SORT_FIELD_MAP = {
        "created_at": "createdAt",
        "priority": "priorityOrder",
        "due_date": "dueDate",
    }
    mongo_sort_field = SORT_FIELD_MAP.get(sort_by, "createdAt")
    mongo_sort_dir = 1 if sort_order == "asc" else -1

    skip = (page - 1) * page_size
    limit = page_size

    total_count = ticket_collection.count_documents(query)
    cursor = (
        ticket_collection.find(query)
        .sort(mongo_sort_field, mongo_sort_dir)
        .skip(skip)
        .limit(limit)
    )

    tickets = []
    for ticket in cursor:
        ticket["_id"] = str(ticket["_id"])
        tickets.append(ticket)

    total_pages = (total_count + page_size - 1) // page_size

    return {
        "data": tickets,
        "pagination": {
            "total": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
        },
        "filters_applied": {
            "status": status,
            "priority": priority,
            "category": category,
            "assigned_agent": assigned_agent,
            "search": search,
            "sort_by": sort_by,
            "sort_order": sort_order,
        },
    }


@router.get("/tickets_summary", tags=["Tickets"])
def get_tickets_summary(current_user: dict = Depends(get_current_user)):
    query = {}

    if current_user["role"] != "super_admin":
        query["userId"] = str(current_user["_id"])

    pipeline = [
        {"$match": query},
        {
            "$group": {
                "_id": None,
                "total_tickets": {"$sum": 1},
                "open_tickets": {
                    "$sum": {"$cond": [{"$eq": ["$status", "open"]}, 1, 0]}
                },
                "in_progress_tickets": {
                    "$sum": {"$cond": [{"$eq": ["$status", "in_progress"]}, 1, 0]}
                },
                "resolved_tickets": {
                    "$sum": {"$cond": [{"$eq": ["$status", "resolved"]}, 1, 0]}
                },
                "closed_tickets": {
                    "$sum": {"$cond": [{"$eq": ["$status", "closed"]}, 1, 0]}
                },
                "high_priority_tickets": {
                    "$sum": {"$cond": [{"$eq": ["$priority", "high"]}, 1, 0]}
                },
                "medium_priority_tickets": {
                    "$sum": {"$cond": [{"$eq": ["$priority", "medium"]}, 1, 0]}
                },
                "low_priority_tickets": {
                    "$sum": {"$cond": [{"$eq": ["$priority", "low"]}, 1, 0]}
                },
            }
        },
        {"$project": {"_id": 0}},
    ]

    summary = list(ticket_collection.aggregate(pipeline))
    return summary[0] if summary else {}


@router.delete("/tickets/{ticket_id}", tags=["Tickets"])
def delete_ticket(ticket_id: str, current_user: dict = Depends(get_current_user)):
    ticket = ticket_collection.find_one({"_id": ObjectId(ticket_id)})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if current_user["role"] != "super_admin" and ticket["userId"] != str(
        current_user["_id"]
    ):
        raise HTTPException(
            status_code=403, detail="You can only delete your own tickets"
        )

    ticket_collection.delete_one({"_id": ObjectId(ticket_id)})

    notification = Notifications(
        ticket_id=ticket_id,
        user_id=str(current_user["_id"]),
        event_type="ticket_deleted",
    )
    notifications_collection.insert_one(notification.dict())

    return {"message": "Ticket deleted successfully"}


@router.patch("/{ticket_id}/move-forward", tags=["Tickets"])
def move_ticket_forward(
    ticket_id: str,
    current_user: dict = Depends(get_current_user),
):
    ticket = ticket_collection.find_one(
        {"_id": _parse_object_id(ticket_id, "ticket_id")}
    )
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if current_user["role"] != "super_admin" and ticket["userId"] != str(
        current_user["_id"]
    ):
        raise HTTPException(
            status_code=403, detail="Only agents or admins can move tickets"
        )

    board = boards_collection.find_one(
        {"_id": _parse_object_id(ticket["board_id"], "board_id")}
    )
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")

    assigned_statuses = board.get("assigned_statuses", [])
    if not assigned_statuses:
        raise HTTPException(status_code=400, detail="Board has no statuses assigned")

    current_status_id = ticket["current_status_id"]
    if current_status_id not in assigned_statuses:
        raise HTTPException(
            status_code=400,
            detail="Ticket's current status is not in this board's statuses",
        )

    current_index = assigned_statuses.index(current_status_id)

    if current_index == len(assigned_statuses) - 1:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Ticket is already at the last status "
                f"'{ticket['current_status_name']}'. "
                "No further movement allowed."
            ),
        )

    next_status_id = assigned_statuses[current_index + 1]
    next_status = statuses_collection.find_one(
        {"_id": _parse_object_id(next_status_id, "status_id")}
    )
    if not next_status:
        raise HTTPException(status_code=404, detail="Next status not found")

    now = datetime.now(timezone.utc)
    update_fields = {
        "current_status_id": next_status_id,
        "current_status_name": next_status["name"],
        "updated_at": now,
    }

    if current_index + 1 == len(assigned_statuses) - 1:
        update_fields["closed_date"] = now

    if current_index + 1 == len(assigned_statuses) - 2:
        update_fields["resolved_date"] = now

    ticket_collection.update_one(
        {"_id": _parse_object_id(ticket_id, "ticket_id")},
        {"$set": update_fields},
    )

    return {
        "ticket_id": ticket_id,
        "ticket_code": ticket["ticket_code"],
        "previous_status": ticket["current_status_name"],
        "current_status": next_status["name"],
        "updated_at": now,
    }
