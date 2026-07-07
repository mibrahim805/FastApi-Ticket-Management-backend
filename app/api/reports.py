from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import get_current_user
from app.db.db import ticket_collection, user_collection

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.get("/agent-tickets", tags=["Reports"])
def report_agent_tickets(current_user: dict = Depends(get_current_user)):
    """
    Report ticket counts per agent (open vs resolved).
    Only super_admin and admin can access this.
    """
    if current_user["role"] not in ["super_admin", "admin"]:
        raise HTTPException(status_code=403, detail="Only admins can access reports")

    pipeline = [
        {"$match": {"assigned_agent": {"$ne": None}}},
        {
            "$group": {
                "_id": "$assigned_agent",
                "total_tickets": {"$sum": 1},
                "open_tickets": {
                    "$sum": {"$cond": [{"$eq": ["$current_status_name", "open"]}, 1, 0]}
                },
                "in_progress_tickets": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$current_status_name", "in_progress"]},
                            1,
                            0,
                        ]
                    }
                },
                "on_hold_tickets": {
                    "$sum": {
                        "$cond": [{"$eq": ["$current_status_name", "on_hold"]}, 1, 0]
                    }
                },
                "resolved_tickets": {
                    "$sum": {
                        "$cond": [{"$eq": ["$current_status_name", "resolved"]}, 1, 0]
                    }
                },
                "closed_tickets": {
                    "$sum": {
                        "$cond": [{"$eq": ["$current_status_name", "closed"]}, 1, 0]
                    }
                },
            }
        },
        {"$sort": {"total_tickets": -1}},
    ]

    results = list(ticket_collection.aggregate(pipeline))

    # Enrich with agent names
    enriched_results = []
    for result in results:
        agent = user_collection.find_one({"_id": result["_id"]})
        enriched_results.append(
            {
                "agent_id": str(result["_id"]),
                "agent_name": agent.get("name", "Unknown") if agent else "Unknown",
                "total_tickets": result["total_tickets"],
                "open_tickets": result["open_tickets"],
                "in_progress_tickets": result["in_progress_tickets"],
                "on_hold_tickets": result["on_hold_tickets"],
                "resolved_tickets": result["resolved_tickets"],
                "closed_tickets": result["closed_tickets"],
            }
        )

    return {"data": enriched_results}


@router.get("/overdue-tickets", tags=["Reports"])
def report_overdue_tickets(current_user: dict = Depends(get_current_user)):
    """
    Report count of overdue tickets.
    Only super_admin and admin can access this.
    """
    if current_user["role"] not in ["super_admin", "admin"]:
        raise HTTPException(status_code=403, detail="Only admins can access reports")

    now = datetime.now(timezone.utc)

    pipeline = [
        {
            "$match": {
                "$expr": {
                    "$and": [
                        {"$lt": ["$due_date", now]},
                        {
                            "$nin": [
                                "$current_status_name",
                                ["resolved", "closed", "reopened"],
                            ]
                        },
                    ]
                }
            }
        },
        {
            "$group": {
                "_id": None,
                "total_overdue": {"$sum": 1},
                "by_priority": {
                    "$push": {"priority": "$priority", "ticket_code": "$ticket_code"}
                },
                "by_agent": {
                    "$push": {"agent": "$assigned_agent", "ticket_code": "$ticket_code"}
                },
            }
        },
    ]

    result = list(ticket_collection.aggregate(pipeline))

    if not result:
        return {
            "total_overdue": 0,
            "by_priority": {},
            "by_agent": {},
        }

    data = result[0]

    # Count by priority
    priority_counts = {}
    for item in data.get("by_priority", []):
        priority = item.get("priority", "unknown")
        priority_counts[priority] = priority_counts.get(priority, 0) + 1

    # Count by agent
    agent_counts = {}
    for item in data.get("by_agent", []):
        agent = item.get("agent", "unassigned")
        if agent:
            agent_counts[agent] = agent_counts.get(agent, 0) + 1
        else:
            agent_counts["unassigned"] = agent_counts.get("unassigned", 0) + 1

    return {
        "total_overdue": data.get("total_overdue", 0),
        "by_priority": priority_counts,
        "by_agent": agent_counts,
    }


@router.get("/avg-resolution-time", tags=["Reports"])
def report_avg_resolution_time(current_user: dict = Depends(get_current_user)):
    """
    Report average resolution time (from creation to resolved_date).
    Only super_admin and admin can access this.
    """
    if current_user["role"] not in ["super_admin", "admin"]:
        raise HTTPException(status_code=403, detail="Only admins can access reports")

    pipeline = [
        {
            "$match": {
                "resolved_date": {"$ne": None},
                "created_date": {"$ne": None},
            }
        },
        {
            "$project": {
                "resolution_time_hours": {
                    "$divide": [
                        {"$subtract": ["$resolved_date", "$created_date"]},
                        3600000,  # Convert milliseconds to hours
                    ]
                },
                "priority": 1,
                "category": 1,
            }
        },
        {
            "$group": {
                "_id": None,
                "avg_resolution_hours": {"$avg": "$resolution_time_hours"},
                "min_resolution_hours": {"$min": "$resolution_time_hours"},
                "max_resolution_hours": {"$max": "$resolution_time_hours"},
                "total_resolved": {"$sum": 1},
                "by_priority": {
                    "$push": {
                        "priority": "$priority",
                        "resolution_time": "$resolution_time_hours",
                    }
                },
            }
        },
    ]

    result = list(ticket_collection.aggregate(pipeline))

    if not result:
        return {
            "avg_resolution_hours": 0,
            "min_resolution_hours": 0,
            "max_resolution_hours": 0,
            "total_resolved": 0,
            "by_priority": {},
        }

    data = result[0]

    # Calculate average by priority
    priority_avg = {}
    for item in data.get("by_priority", []):
        priority = item.get("priority", "unknown")
        if priority not in priority_avg:
            priority_avg[priority] = []
        priority_avg[priority].append(item.get("resolution_time", 0))

    priority_averages = {
        priority: sum(times) / len(times) for priority, times in priority_avg.items()
    }

    return {
        "avg_resolution_hours": round(data.get("avg_resolution_hours", 0), 2),
        "min_resolution_hours": round(data.get("min_resolution_hours", 0), 2),
        "max_resolution_hours": round(data.get("max_resolution_hours", 0), 2),
        "total_resolved": data.get("total_resolved", 0),
        "by_priority": {k: round(v, 2) for k, v in priority_averages.items()},
    }
