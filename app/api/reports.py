from datetime import datetime, timezone
from typing import Annotated

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.auth import get_current_user
from app.api.permissions import (
    ADMIN_ROLES,
    Capability,
    ensure_role,
    get_accessible_project_ids,
    parse_object_id,
    require_project_membership,
    require_role,
)
from app.db.db import (
    project_members_collection,
    projects_collection,
    sprints_collection,
    ticket_collection,
    user_collection,
)

router = APIRouter(prefix="/reports", tags=["Reports"])

_require_admin = require_role(
    *ADMIN_ROLES,
    detail="Only admins can access reports",
)

TERMINAL_STATUS_NAMES = frozenset({"resolved", "closed", "done"})


def _normalized_status(ticket: dict) -> str:
    return (
        str(ticket.get("current_status_name") or "unknown")
        .strip()
        .casefold()
        .replace(" ", "_")
        .replace("-", "_")
    )


def _is_open_ticket(ticket: dict) -> bool:
    if ticket.get("closed_date") is not None or ticket.get("resolved_date") is not None:
        return False
    return _normalized_status(ticket) not in TERMINAL_STATUS_NAMES


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_overdue_ticket(ticket: dict, now: datetime) -> bool:
    due_date = ticket.get("due_date")
    return bool(due_date and _is_open_ticket(ticket) and _as_aware_utc(due_date) < now)


def _ticket_recency(ticket: dict) -> datetime:
    value = ticket.get("updated_at") or ticket.get("created_date")
    if isinstance(value, datetime):
        return _as_aware_utc(value)
    return datetime.min.replace(tzinfo=timezone.utc)


def _serialize_dashboard_ticket(ticket: dict, now: datetime) -> dict:
    ticket_id = ticket.get("_id")
    return {
        "ticket_id": str(ticket_id) if isinstance(ticket_id, ObjectId) else ticket_id,
        "ticket_code": ticket.get("ticket_code"),
        "project_id": str(ticket.get("project_id")),
        "title": ticket.get("title"),
        "priority": ticket.get("priority"),
        "current_status_name": ticket.get("current_status_name"),
        "sprint_id": ticket.get("sprint_id"),
        "story_points": ticket.get("story_points"),
        "due_date": ticket.get("due_date"),
        "is_overdue": _is_overdue_ticket(ticket, now),
    }


def _empty_user_dashboard(user_id: str) -> dict:
    return {
        "user_id": user_id,
        "project_count": 0,
        "counts": {
            "my_open_issues": 0,
            "assigned_to_me": 0,
            "overdue_assigned_to_me": 0,
        },
        "my_open_issues": [],
        "assigned_to_me": [],
    }


def _story_points(ticket: dict) -> int:
    value = ticket.get("story_points")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _sprint_recency(sprint: dict) -> datetime:
    value = (
        sprint.get("completed_at") or sprint.get("end_date") or sprint.get("created_at")
    )
    if isinstance(value, datetime):
        return _as_aware_utc(value)
    return datetime.min.replace(tzinfo=timezone.utc)


def _require_project_report_access(
    project_object_id: ObjectId,
    current_user: dict,
) -> None:
    if current_user.get("role") not in ADMIN_ROLES:
        require_project_membership(
            project_object_id,
            current_user,
            project_members_collection,
            capability=Capability.VIEW_REPORTS,
        )


@router.get("/my-dashboard")
def get_my_dashboard(
    limit: Annotated[
        int,
        Query(ge=1, le=100, description="Issues per dashboard list"),
    ] = 20,
    current_user: dict = Depends(get_current_user),
):
    """Return active issues created by or assigned to the current user."""
    user_id = str(parse_object_id(current_user.get("_id"), "user_id"))
    project_ids = get_accessible_project_ids(
        current_user,
        project_members_collection,
        projects_collection,
    )
    if not project_ids:
        return _empty_user_dashboard(user_id)

    candidates = list(
        ticket_collection.find(
            {
                "project_id": {"$in": project_ids},
                "$or": [
                    {"userId": user_id},
                    {"assigned_agent": user_id},
                ],
            }
        )
    )
    my_open_issues = [
        ticket
        for ticket in candidates
        if str(ticket.get("userId")) == user_id and _is_open_ticket(ticket)
    ]
    assigned_to_me = [
        ticket
        for ticket in candidates
        if str(ticket.get("assigned_agent")) == user_id and _is_open_ticket(ticket)
    ]
    my_open_issues.sort(key=_ticket_recency, reverse=True)
    assigned_to_me.sort(key=_ticket_recency, reverse=True)
    now = datetime.now(timezone.utc)

    return {
        "user_id": user_id,
        "project_count": len(project_ids),
        "counts": {
            "my_open_issues": len(my_open_issues),
            "assigned_to_me": len(assigned_to_me),
            "overdue_assigned_to_me": sum(
                _is_overdue_ticket(ticket, now) for ticket in assigned_to_me
            ),
        },
        "my_open_issues": [
            _serialize_dashboard_ticket(ticket, now)
            for ticket in my_open_issues[:limit]
        ],
        "assigned_to_me": [
            _serialize_dashboard_ticket(ticket, now)
            for ticket in assigned_to_me[:limit]
        ],
    }


@router.get("/projects/{project_id}/summary")
def report_project_summary(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Return workflow-independent ticket counts for one project."""
    project_object_id = parse_object_id(project_id, "project_id")
    project = projects_collection.find_one({"_id": project_object_id})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    _require_project_report_access(project_object_id, current_user)

    tickets = list(ticket_collection.find({"project_id": str(project_object_id)}))
    by_status = {}
    by_priority = {}
    assigned_tickets = 0
    overdue_tickets = 0
    open_tickets = 0
    now = datetime.now(timezone.utc)
    for ticket in tickets:
        ticket_status = _normalized_status(ticket)
        priority = str(ticket.get("priority") or "unknown").casefold()
        by_status[ticket_status] = by_status.get(ticket_status, 0) + 1
        by_priority[priority] = by_priority.get(priority, 0) + 1
        assigned_tickets += bool(ticket.get("assigned_agent"))
        open_tickets += _is_open_ticket(ticket)
        overdue_tickets += _is_overdue_ticket(ticket, now)

    backlog_tickets = [ticket for ticket in tickets if not ticket.get("sprint_id")]
    active_sprint = sprints_collection.find_one(
        {"project_id": project_object_id, "status": "active"}
    )
    active_sprint_summary = None
    if active_sprint:
        active_sprint_id = str(active_sprint["_id"])
        active_tickets = [
            ticket
            for ticket in tickets
            if str(ticket.get("sprint_id")) == active_sprint_id
        ]
        active_completed = [
            ticket for ticket in active_tickets if not _is_open_ticket(ticket)
        ]
        active_sprint_summary = {
            "sprint_id": active_sprint_id,
            "name": active_sprint.get("name"),
            "start_date": active_sprint.get("start_date"),
            "end_date": active_sprint.get("end_date"),
            "total_tickets": len(active_tickets),
            "completed_tickets": len(active_completed),
            "committed_story_points": sum(
                _story_points(ticket) for ticket in active_tickets
            ),
            "completed_story_points": sum(
                _story_points(ticket) for ticket in active_completed
            ),
        }

    return {
        "project": {
            "project_id": str(project_object_id),
            "code": project.get("code"),
            "name": project.get("name"),
        },
        "total_tickets": len(tickets),
        "open_tickets": open_tickets,
        "assigned_tickets": assigned_tickets,
        "unassigned_tickets": len(tickets) - assigned_tickets,
        "overdue_tickets": overdue_tickets,
        "by_status": by_status,
        "by_priority": by_priority,
        "agile": {
            "backlog_tickets": len(backlog_tickets),
            "backlog_story_points": sum(
                _story_points(ticket) for ticket in backlog_tickets
            ),
            "active_sprint": active_sprint_summary,
        },
    }


@router.get("/projects/{project_id}/velocity")
def report_project_velocity(
    project_id: str,
    limit: Annotated[
        int,
        Query(ge=1, le=100, description="Most recent completed sprints to include"),
    ] = 10,
    current_user: dict = Depends(get_current_user),
):
    """Return completed story points per sprint for a project."""
    project_object_id = parse_object_id(project_id, "project_id")
    project = projects_collection.find_one({"_id": project_object_id})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    _require_project_report_access(project_object_id, current_user)

    completed_sprints = list(
        sprints_collection.find(
            {"project_id": project_object_id, "status": "completed"}
        )
    )
    completed_sprints.sort(key=_sprint_recency, reverse=True)
    completed_sprints = list(reversed(completed_sprints[:limit]))

    series = []
    for sprint in completed_sprints:
        metrics = sprint.get("metrics") or {}
        committed_points = int(metrics.get("committed_story_points") or 0)
        completed_points = int(metrics.get("completed_story_points") or 0)
        completion_rate = (
            round(completed_points / committed_points * 100, 2)
            if committed_points
            else 0.0
        )
        series.append(
            {
                "sprint_id": str(sprint["_id"]),
                "name": sprint.get("name"),
                "start_date": sprint.get("start_date"),
                "end_date": sprint.get("end_date"),
                "completed_at": sprint.get("completed_at"),
                "committed_tickets": int(metrics.get("committed_tickets") or 0),
                "completed_tickets": int(metrics.get("completed_tickets") or 0),
                "committed_story_points": committed_points,
                "completed_story_points": completed_points,
                "velocity": completed_points,
                "completion_rate": completion_rate,
            }
        )

    velocities = [sprint["velocity"] for sprint in series]
    latest_velocity = velocities[-1] if velocities else 0
    previous_velocity = velocities[-2] if len(velocities) > 1 else None
    velocity_delta = (
        latest_velocity - previous_velocity if previous_velocity is not None else None
    )
    if velocity_delta is None or velocity_delta == 0:
        trend = "flat"
    elif velocity_delta > 0:
        trend = "up"
    else:
        trend = "down"

    return {
        "project": {
            "project_id": str(project_object_id),
            "code": project.get("code"),
            "name": project.get("name"),
        },
        "completed_sprints": len(series),
        "average_velocity": (
            round(sum(velocities) / len(velocities), 2) if velocities else 0.0
        ),
        "latest_velocity": latest_velocity,
        "velocity_delta": velocity_delta,
        "trend": trend,
        "sprints": series,
    }


@router.get("/agent-tickets", tags=["Reports"])
def report_agent_tickets(current_user: dict = Depends(_require_admin)):
    """
    Report ticket counts per agent (open vs resolved).
    Only super_admin and admin can access this.
    """
    ensure_role(
        current_user,
        ADMIN_ROLES,
        detail="Only admins can access reports",
    )

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
def report_overdue_tickets(current_user: dict = Depends(_require_admin)):
    """
    Report count of overdue tickets.
    Only super_admin and admin can access this.
    """
    ensure_role(
        current_user,
        ADMIN_ROLES,
        detail="Only admins can access reports",
    )

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
def report_avg_resolution_time(current_user: dict = Depends(_require_admin)):
    """
    Report average resolution time (from creation to resolved_date).
    Only super_admin and admin can access this.
    """
    ensure_role(
        current_user,
        ADMIN_ROLES,
        detail="Only admins can access reports",
    )

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
