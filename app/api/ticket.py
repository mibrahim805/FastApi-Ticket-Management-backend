from bson import ObjectId
from app.api.auth import get_current_user
from app.models.notifications import Notifications
from app.models.ticket import TicketCreate, TicketUpdate, AssignAgent
from fastapi import APIRouter, Depends, Query, HTTPException
from app.db.db import ticket_collection, comments_collection, notifications_collection, boards_collection
from datetime import datetime, timedelta, timezone
from typing import Optional
import re


router = APIRouter()




def calculate_due_date(priority: str) -> datetime:
    now = datetime.now(timezone.utc)

    sla_days = {
        "high": 1,
        "medium": 3,
        "low": 7,
    }

    return now + timedelta(days=sla_days[priority])



@router.post("/tickets", tags=["Tickets"])
def create_ticket(payload: TicketCreate, current_user: dict = Depends(get_current_user)):
    board = boards_collection.find_one({"_id": ObjectId(payload.board_id)})
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")
    if current_user["role"] != "super_admin" and board["assigned_agent"] != str(current_user["_id"]):
        raise HTTPException(status_code=403, detail="You can only create tickets for boards assigned to you")
    next_number = board.get("ticket_counter",0)+1
    ticket_id = f"ticket-{next_number}"
    ticket_code = f"{board.get('code', 'BOARD')}-{next_number}"
    boards_collection.update_one({"_id": ObjectId(payload.board_id)}, {"$set": {"ticket_counter": next_number}})
    ticket = {
        "board_id": str(board.get("_id")),
        "ticket_id": ticket_id,
        "ticket_code": ticket_code,
        "board_code": board.get("code"),
        "userId": str(current_user["_id"]),
        "title": payload.title,
        "description": payload.description,
        "category": payload.category.value,
        "priority": payload.priority.value,
        "status": "open",
        "created_date": datetime.now(timezone.utc),
        "due_date": calculate_due_date(payload.priority.value),
    }


    result = ticket_collection.insert_one(ticket)
    ticket["_id"] = str(result.inserted_id)

    notification = Notifications(
        ticket_id=str(result.inserted_id),
        user_id=str(current_user["_id"]),
        event_type="ticket_created"
    )
    notification_record = notifications_collection.insert_one(notification.dict())

    ticket["notification_id"] = str(notification_record.inserted_id)

    return ticket



@router.get("/tickets/detail", tags=["Tickets"])
def get_ticket_details(current_user: dict = Depends(get_current_user), ticket_id=None):
    ticket = ticket_collection.find_one({"_id": ObjectId(ticket_id)})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if current_user["role"] != "super_admin" and ticket["userId"] != str(current_user["_id"]):
        raise HTTPException(status_code=403, detail="You can only view your own tickets")
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
def update_ticket(ticket_id: str,data: TicketUpdate,current_user: dict = Depends(get_current_user)):
    ticket = ticket_collection.find_one({"_id": ObjectId(ticket_id)})
    if not ticket:
        raise HTTPException(status_code=404,detail="Ticket not found")
    if current_user["role"] != "super_admin"and ticket["userId"] != str(current_user["_id"]):
        raise HTTPException(status_code=403,detail="You can only update your own tickets")
    update_data = data.model_dump(exclude_none=True)
    update_data["updated_by"] = str(current_user["_id"])
    update_data["updated_at"] = datetime.utcnow()
    ticket_collection.update_one(
        {"_id": ObjectId(ticket_id)},
        {"$set": update_data}
    )

    notification = Notifications(
        ticket_id=ticket_id,
        user_id=str(current_user["_id"]),
        event_type="ticket_updated"
    )

    notifications_collection.insert_one(notification.model_dump())
    return {"message": "Ticket updated successfully"}



@router.put("/tickets/{ticket_id}/assign", tags= ["Tickets"])
def assign_ticket(ticket_id: str,data: AssignAgent,current_user: dict = Depends(get_current_user)):
    ticket = ticket_collection.find_one({"_id": ObjectId(ticket_id)})
    if not ticket:
        raise HTTPException(status_code=404,detail="Ticket not found")
    if current_user["role"] != "super_admin":
        raise HTTPException(status_code=403,detail="only super admin can assign ticket")

    if ticket.get("assignedAgent")and ticket["assignedAgent"] != data.agent_id and current_user["role"] != "super_admin":
        raise HTTPException(status_code=403,detail="Only super admin can reassign tickets")
    ticket_collection.update_one(
        {"_id": ObjectId(ticket_id)},
        {
            "$set": {
                "assignedAgent": data.agent_id,
                "updatedBy": str(current_user["_id"])
            }
        }
    )

    notification = Notifications(
        ticket_id=ticket_id,
        user_id=str(current_user["_id"]),
        event_type="ticket_assigned"
    )
    notifications_collection.insert_one(notification.dict())

    return {"message": "Ticket assigned successfully"}



@router.get("/tickets/{board_code}", tags=["Tickets"])
def get_tickets(
    board_code:str,
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(10, ge=1, le=100, description="Results per page"),
    status: Optional[str] = Query(None, description="Filter by status (e.g. open, closed, pending)"),
    priority: Optional[str] = Query(None, description="Filter by priority (e.g. low, medium, high)"),
    category: Optional[str] = Query(None, description="Filter by category"),
    assigned_agent: Optional[str] = Query(None, description="Filter by assigned agent ID"),
    search: Optional[str] = Query(None, description="Search by title or ticket number"),
    sort_by: Optional[str] = Query("created_at", regex="^(created_at|priority|due_date)$", description="Sort field"),
    sort_order: Optional[str] = Query("desc", regex="^(asc|desc)$", description="Sort direction"),
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
        query["assignedAgent"] = assigned_agent

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
        ticket_collection
        .find(query)
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
                }
            }
        },
        {
            "$project": {
                "_id": 0
            }
        }
    ]

    summary = list(ticket_collection.aggregate(pipeline))
    return summary[0] if summary else {}



@router.delete("/tickets/{ticket_id}", tags=["Tickets"])
def delete_ticket(ticket_id: str, current_user: dict = Depends(get_current_user)):
    ticket = ticket_collection.find_one({"_id": ObjectId(ticket_id)})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if current_user["role"] != "super_admin" and ticket["userId"] != str(current_user["_id"]):
        raise HTTPException(status_code=403, detail="You can only delete your own tickets")

    ticket_collection.delete_one({"_id": ObjectId(ticket_id)})

    notification = Notifications(
        ticket_id=ticket_id,
        user_id=str(current_user["_id"]),
        event_type="ticket_deleted"
    )
    notifications_collection.insert_one(notification.dict())

    return {"message": "Ticket deleted successfully"}
