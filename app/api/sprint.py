from datetime import datetime, timezone
from typing import Annotated, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pymongo.errors import DuplicateKeyError

from app.api.activity import log_activity
from app.api.auth import get_current_user
from app.api.permissions import (
    Capability,
    parse_object_id,
    require_project_membership,
)
from app.db.db import (
    project_members_collection,
    projects_collection,
    sprints_collection,
    statuses_collection,
    ticket_collection,
)
from app.models.sprint import (
    SprintCreate,
    SprintStatus,
    SprintUpdate,
    StoryPointsUpdate,
    TicketPositionUpdate,
    TicketSprintUpdate,
)

router = APIRouter(tags=["Sprints"])

DONE_STATUS_NAMES = frozenset({"done", "closed", "resolved"})


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalize_optional_date(value: Optional[datetime]) -> Optional[datetime]:
    return _as_aware_utc(value) if value is not None else None


def _validate_date_range(
    start_date: Optional[datetime],
    end_date: Optional[datetime],
) -> None:
    if (
        start_date is not None
        and end_date is not None
        and _as_aware_utc(end_date) <= _as_aware_utc(start_date)
    ):
        raise HTTPException(
            status_code=400,
            detail="end_date must be later than start_date",
        )


def _get_project_or_404(project_id: str | ObjectId) -> tuple[ObjectId, dict]:
    project_object_id = parse_object_id(project_id, "project_id")
    project = projects_collection.find_one({"_id": project_object_id})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project_object_id, project


def _get_sprint_or_404(sprint_id: str | ObjectId) -> tuple[ObjectId, dict]:
    sprint_object_id = parse_object_id(sprint_id, "sprint_id")
    sprint = sprints_collection.find_one({"_id": sprint_object_id})
    if not sprint:
        raise HTTPException(status_code=404, detail="Sprint not found")
    return sprint_object_id, sprint


def _get_ticket_or_404(ticket_id: str | ObjectId) -> tuple[ObjectId, dict]:
    ticket_object_id = parse_object_id(ticket_id, "ticket_id")
    ticket = ticket_collection.find_one({"_id": ticket_object_id})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket_object_id, ticket


def _require_project_access(project_id: ObjectId, current_user: dict) -> dict:
    return require_project_membership(
        project_id,
        current_user,
        project_members_collection,
        capability=Capability.VIEW_PROJECT,
        role_detail="You do not have permission to view this project",
        allow_super_admin=True,
    )


def _require_project_work(project_id: ObjectId, current_user: dict) -> dict:
    return require_project_membership(
        project_id,
        current_user,
        project_members_collection,
        capability=Capability.MANAGE_TICKET_WORK,
        role_detail="Only project leads or members can manage sprint tickets",
        allow_super_admin=True,
    )


def _require_project_lead(project_id: ObjectId, current_user: dict) -> dict:
    return require_project_membership(
        project_id,
        current_user,
        project_members_collection,
        capability=Capability.MANAGE_SPRINTS,
        role_detail="Only project leads can manage sprints",
        allow_super_admin=True,
    )


def _serialize_sprint(sprint: dict) -> dict:
    serialized = dict(sprint)
    serialized["_id"] = str(serialized["_id"])
    serialized["project_id"] = str(serialized["project_id"])
    serialized.pop("name_key", None)
    return serialized


def _serialize_ticket(ticket: dict) -> dict:
    serialized = dict(ticket)
    if isinstance(serialized.get("_id"), ObjectId):
        serialized["_id"] = str(serialized["_id"])
    serialized.setdefault("sprint_id", None)
    serialized.setdefault("story_points", None)
    serialized.setdefault("backlog_position", None)
    serialized.setdefault("board_position", 0)
    return serialized


def _position_value(ticket: dict, field: str) -> tuple[int, int, str]:
    value = ticket.get(field)
    has_position = isinstance(value, int) and not isinstance(value, bool) and value >= 0
    return (
        0 if has_position else 1,
        value if has_position else 0,
        str(ticket.get("ticket_code") or ticket.get("_id")),
    )


def _sorted_tickets(tickets: list[dict], field: str) -> list[dict]:
    return sorted(tickets, key=lambda ticket: _position_value(ticket, field))


def _story_points(ticket: dict) -> int:
    value = ticket.get("story_points")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _is_done_ticket(ticket: dict, project: dict) -> bool:
    assigned_statuses = [str(value) for value in project.get("assigned_statuses", [])]
    if assigned_statuses and str(ticket.get("current_status_id")) == (
        assigned_statuses[-1]
    ):
        return True
    if ticket.get("closed_date") is not None or ticket.get("resolved_date") is not None:
        return True
    status_name = (
        str(ticket.get("current_status_name") or "")
        .strip()
        .casefold()
        .replace(" ", "_")
        .replace("-", "_")
    )
    return status_name in DONE_STATUS_NAMES


def _scope_metrics(tickets: list[dict], project: dict) -> dict:
    completed = [ticket for ticket in tickets if _is_done_ticket(ticket, project)]
    committed_points = sum(_story_points(ticket) for ticket in tickets)
    completed_points = sum(_story_points(ticket) for ticket in completed)
    return {
        "committed_tickets": len(tickets),
        "completed_tickets": len(completed),
        "incomplete_tickets": len(tickets) - len(completed),
        "committed_story_points": committed_points,
        "completed_story_points": completed_points,
        "incomplete_story_points": committed_points - completed_points,
        "unestimated_tickets": sum(
            ticket.get("story_points") is None for ticket in tickets
        ),
    }


def _next_backlog_position(project_id: ObjectId) -> int:
    backlog = list(
        ticket_collection.find({"project_id": str(project_id), "sprint_id": None})
    )
    positions = [
        ticket.get("backlog_position")
        for ticket in backlog
        if isinstance(ticket.get("backlog_position"), int)
        and not isinstance(ticket.get("backlog_position"), bool)
    ]
    return max(max(positions, default=-1) + 1, len(backlog))


def _next_board_position(
    project_id: ObjectId,
    status_id: str,
    sprint_id: Optional[str],
) -> int:
    column = list(
        ticket_collection.find(
            {
                "project_id": str(project_id),
                "sprint_id": sprint_id,
                "current_status_id": status_id,
            }
        )
    )
    positions = [
        ticket.get("board_position")
        for ticket in column
        if isinstance(ticket.get("board_position"), int)
        and not isinstance(ticket.get("board_position"), bool)
    ]
    return max(max(positions, default=-1) + 1, len(column))


def _renumber_tickets(
    tickets: list[dict],
    field: str,
    current_user: dict,
) -> list[dict]:
    now = datetime.now(timezone.utc)
    for position, ticket in enumerate(tickets):
        if ticket.get(field) != position:
            ticket_collection.update_one(
                {"_id": ticket["_id"]},
                {
                    "$set": {
                        field: position,
                        "updated_at": now,
                        "updated_by": str(current_user["_id"]),
                    }
                },
            )
            ticket[field] = position
    return tickets


def _move_ticket_in_order(
    tickets: list[dict],
    ticket_id: ObjectId,
    position: int,
    field: str,
    current_user: dict,
) -> list[dict]:
    ordered = _sorted_tickets(tickets, field)
    moving_ticket = next(
        (ticket for ticket in ordered if ticket.get("_id") == ticket_id),
        None,
    )
    if moving_ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found in this scope")
    ordered.remove(moving_ticket)
    ordered.insert(min(position, len(ordered)), moving_ticket)
    return _renumber_tickets(ordered, field, current_user)


def _ensure_sprint_is_mutable(sprint: dict) -> None:
    if sprint.get("status") == SprintStatus.COMPLETED.value:
        raise HTTPException(
            status_code=409,
            detail="Completed sprint tickets and estimates are locked",
        )


@router.post(
    "/projects/{project_id}/sprints",
    status_code=status.HTTP_201_CREATED,
)
def create_sprint(
    project_id: str,
    payload: SprintCreate,
    current_user: dict = Depends(get_current_user),
):
    project_object_id, _ = _get_project_or_404(project_id)
    _require_project_lead(project_object_id, current_user)

    name_key = payload.name.casefold()
    if sprints_collection.find_one(
        {"project_id": project_object_id, "name_key": name_key}
    ):
        raise HTTPException(
            status_code=409,
            detail="A sprint with this name already exists in the project",
        )

    now = datetime.now(timezone.utc)
    sprint = {
        "project_id": project_object_id,
        "name": payload.name,
        "name_key": name_key,
        "goal": payload.goal,
        "status": SprintStatus.PLANNED.value,
        "start_date": _normalize_optional_date(payload.start_date),
        "end_date": _normalize_optional_date(payload.end_date),
        "created_at": now,
        "created_by": str(current_user["_id"]),
        "updated_at": now,
        "updated_by": str(current_user["_id"]),
    }
    try:
        result = sprints_collection.insert_one(sprint)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="A sprint with this name already exists in the project",
        )
    sprint["_id"] = result.inserted_id
    return _serialize_sprint(sprint)


@router.get("/projects/{project_id}/sprints")
def list_sprints(
    project_id: str,
    sprint_status: Annotated[
        Optional[SprintStatus],
        Query(alias="status", description="Filter by sprint lifecycle status"),
    ] = None,
    current_user: dict = Depends(get_current_user),
):
    project_object_id, project = _get_project_or_404(project_id)
    _require_project_access(project_object_id, current_user)

    query = {"project_id": project_object_id}
    if sprint_status is not None:
        query["status"] = sprint_status.value
    sprints = list(sprints_collection.find(query))
    status_order = {
        SprintStatus.ACTIVE.value: 0,
        SprintStatus.PLANNED.value: 1,
        SprintStatus.COMPLETED.value: 2,
    }
    sprints.sort(
        key=lambda sprint: _as_aware_utc(sprint.get("created_at") or datetime.min),
        reverse=True,
    )
    sprints.sort(key=lambda sprint: status_order.get(sprint.get("status"), 3))

    response = []
    for sprint in sprints:
        serialized = _serialize_sprint(sprint)
        scope = list(ticket_collection.find({"sprint_id": str(sprint["_id"])}))
        serialized["current_scope"] = _scope_metrics(scope, project)
        response.append(serialized)
    return response


@router.get("/sprints/{sprint_id}")
def get_sprint(
    sprint_id: str,
    current_user: dict = Depends(get_current_user),
):
    _, sprint = _get_sprint_or_404(sprint_id)
    project_object_id, project = _get_project_or_404(sprint["project_id"])
    _require_project_access(project_object_id, current_user)

    tickets = _sorted_tickets(
        list(ticket_collection.find({"sprint_id": str(sprint["_id"])})),
        "board_position",
    )
    response = _serialize_sprint(sprint)
    response["current_scope"] = _scope_metrics(tickets, project)
    response["tickets"] = [_serialize_ticket(ticket) for ticket in tickets]
    return response


@router.patch("/sprints/{sprint_id}")
def update_sprint(
    sprint_id: str,
    payload: SprintUpdate,
    current_user: dict = Depends(get_current_user),
):
    sprint_object_id, sprint = _get_sprint_or_404(sprint_id)
    project_object_id, _ = _get_project_or_404(sprint["project_id"])
    _require_project_lead(project_object_id, current_user)
    _ensure_sprint_is_mutable(sprint)

    update = payload.model_dump(exclude_unset=True)
    if not update:
        raise HTTPException(status_code=400, detail="No sprint fields supplied")

    start_date = _normalize_optional_date(
        update.get("start_date", sprint.get("start_date"))
    )
    end_date = _normalize_optional_date(update.get("end_date", sprint.get("end_date")))
    _validate_date_range(start_date, end_date)
    if "start_date" in update:
        update["start_date"] = start_date
    if "end_date" in update:
        update["end_date"] = end_date

    if "name" in update:
        update["name_key"] = update["name"].casefold()
        duplicate = sprints_collection.find_one(
            {"project_id": project_object_id, "name_key": update["name_key"]}
        )
        if duplicate and duplicate.get("_id") != sprint_object_id:
            raise HTTPException(
                status_code=409,
                detail="A sprint with this name already exists in the project",
            )

    update["updated_at"] = datetime.now(timezone.utc)
    update["updated_by"] = str(current_user["_id"])
    try:
        sprints_collection.update_one({"_id": sprint_object_id}, {"$set": update})
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="A sprint with this name already exists in the project",
        )
    sprint.update(update)
    return _serialize_sprint(sprint)


@router.post("/sprints/{sprint_id}/start")
def start_sprint(
    sprint_id: str,
    current_user: dict = Depends(get_current_user),
):
    sprint_object_id, sprint = _get_sprint_or_404(sprint_id)
    project_object_id, project = _get_project_or_404(sprint["project_id"])
    _require_project_lead(project_object_id, current_user)

    if sprint.get("status") != SprintStatus.PLANNED.value:
        raise HTTPException(
            status_code=409,
            detail="Only a planned sprint can be started",
        )
    active_sprint = sprints_collection.find_one(
        {"project_id": project_object_id, "status": SprintStatus.ACTIVE.value}
    )
    if active_sprint:
        raise HTTPException(
            status_code=409,
            detail="This project already has an active sprint",
        )

    now = datetime.now(timezone.utc)
    start_date = _normalize_optional_date(sprint.get("start_date")) or now
    end_date = _normalize_optional_date(sprint.get("end_date"))
    _validate_date_range(start_date, end_date)
    scope = list(ticket_collection.find({"sprint_id": str(sprint_object_id)}))
    scope_metrics = _scope_metrics(scope, project)
    update = {
        "status": SprintStatus.ACTIVE.value,
        "start_date": start_date,
        "started_at": now,
        "commitment": {
            "tickets": scope_metrics["committed_tickets"],
            "story_points": scope_metrics["committed_story_points"],
            "unestimated_tickets": scope_metrics["unestimated_tickets"],
            "ticket_ids": [str(ticket["_id"]) for ticket in scope],
        },
        "updated_at": now,
        "updated_by": str(current_user["_id"]),
    }
    try:
        sprints_collection.update_one({"_id": sprint_object_id}, {"$set": update})
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="This project already has an active sprint",
        )
    sprint.update(update)
    return _serialize_sprint(sprint)


def _move_tickets_to_backlog(
    tickets: list[dict],
    project_id: ObjectId,
    current_user: dict,
) -> list[str]:
    if not tickets:
        return []

    next_backlog_position = _next_backlog_position(project_id)
    next_column_positions: dict[str, int] = {}
    moved_ids = []
    now = datetime.now(timezone.utc)
    for ticket in _sorted_tickets(tickets, "board_position"):
        status_id = str(ticket.get("current_status_id"))
        if status_id not in next_column_positions:
            next_column_positions[status_id] = _next_board_position(
                project_id,
                status_id,
                None,
            )
        ticket_collection.update_one(
            {"_id": ticket["_id"]},
            {
                "$set": {
                    "sprint_id": None,
                    "backlog_position": next_backlog_position,
                    "board_position": next_column_positions[status_id],
                    "updated_at": now,
                    "updated_by": str(current_user["_id"]),
                }
            },
        )
        moved_ids.append(str(ticket["_id"]))
        next_backlog_position += 1
        next_column_positions[status_id] += 1
    return moved_ids


@router.post("/sprints/{sprint_id}/complete")
def complete_sprint(
    sprint_id: str,
    current_user: dict = Depends(get_current_user),
):
    sprint_object_id, sprint = _get_sprint_or_404(sprint_id)
    project_object_id, project = _get_project_or_404(sprint["project_id"])
    _require_project_lead(project_object_id, current_user)

    if sprint.get("status") != SprintStatus.ACTIVE.value:
        raise HTTPException(
            status_code=409,
            detail="Only an active sprint can be completed",
        )

    scope = list(ticket_collection.find({"sprint_id": str(sprint_object_id)}))
    metrics = _scope_metrics(scope, project)
    final_scope_tickets = metrics["committed_tickets"]
    final_scope_points = metrics["committed_story_points"]
    commitment = sprint.get("commitment") or {
        "tickets": final_scope_tickets,
        "story_points": final_scope_points,
        "unestimated_tickets": metrics["unestimated_tickets"],
    }
    metrics.update(
        {
            "committed_tickets": int(commitment.get("tickets") or 0),
            "committed_story_points": int(commitment.get("story_points") or 0),
            "committed_unestimated_tickets": int(
                commitment.get("unestimated_tickets") or 0
            ),
            "final_scope_tickets": final_scope_tickets,
            "final_scope_story_points": final_scope_points,
            "scope_change_tickets": final_scope_tickets
            - int(commitment.get("tickets") or 0),
            "scope_change_story_points": final_scope_points
            - int(commitment.get("story_points") or 0),
        }
    )
    incomplete = [ticket for ticket in scope if not _is_done_ticket(ticket, project)]
    completed = [ticket for ticket in scope if _is_done_ticket(ticket, project)]
    moved_ids = _move_tickets_to_backlog(
        incomplete,
        project_object_id,
        current_user,
    )

    now = datetime.now(timezone.utc)
    update = {
        "status": SprintStatus.COMPLETED.value,
        "completed_at": now,
        "updated_at": now,
        "updated_by": str(current_user["_id"]),
        "metrics": metrics,
        "committed_ticket_ids": list(
            commitment.get(
                "ticket_ids",
                [str(ticket["_id"]) for ticket in scope],
            )
        ),
        "completed_ticket_ids": [str(ticket["_id"]) for ticket in completed],
        "incomplete_ticket_ids": [str(ticket["_id"]) for ticket in incomplete],
    }
    sprints_collection.update_one({"_id": sprint_object_id}, {"$set": update})
    sprint.update(update)
    return {
        "sprint": _serialize_sprint(sprint),
        "velocity": metrics["completed_story_points"],
        "moved_to_backlog": moved_ids,
    }


@router.delete("/sprints/{sprint_id}")
def delete_sprint(
    sprint_id: str,
    current_user: dict = Depends(get_current_user),
):
    sprint_object_id, sprint = _get_sprint_or_404(sprint_id)
    project_object_id, _ = _get_project_or_404(sprint["project_id"])
    _require_project_lead(project_object_id, current_user)
    if sprint.get("status") != SprintStatus.PLANNED.value:
        raise HTTPException(
            status_code=409,
            detail="Only a planned sprint can be deleted",
        )

    tickets = list(ticket_collection.find({"sprint_id": str(sprint_object_id)}))
    moved_ids = _move_tickets_to_backlog(tickets, project_object_id, current_user)
    sprints_collection.delete_one({"_id": sprint_object_id})
    return {
        "message": "Sprint deleted",
        "moved_to_backlog": moved_ids,
    }


@router.get("/projects/{project_id}/backlog")
def get_backlog(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    project_object_id, _ = _get_project_or_404(project_id)
    _require_project_access(project_object_id, current_user)
    tickets = _sorted_tickets(
        list(
            ticket_collection.find(
                {"project_id": str(project_object_id), "sprint_id": None}
            )
        ),
        "backlog_position",
    )
    return {
        "project_id": str(project_object_id),
        "total_tickets": len(tickets),
        "total_story_points": sum(_story_points(ticket) for ticket in tickets),
        "unestimated_tickets": sum(
            ticket.get("story_points") is None for ticket in tickets
        ),
        "tickets": [_serialize_ticket(ticket) for ticket in tickets],
    }


@router.get("/projects/{project_id}/board")
def get_sprint_board(
    project_id: str,
    sprint_id: Annotated[
        Optional[str],
        Query(description="Sprint to display; defaults to the active sprint"),
    ] = None,
    backlog: Annotated[
        bool,
        Query(description="Display backlog tickets in status columns"),
    ] = False,
    current_user: dict = Depends(get_current_user),
):
    project_object_id, project = _get_project_or_404(project_id)
    _require_project_access(project_object_id, current_user)
    if sprint_id is not None and backlog:
        raise HTTPException(
            status_code=400,
            detail="sprint_id and backlog=true cannot be used together",
        )

    selected_sprint = None
    selected_sprint_id = None
    if sprint_id is not None:
        _, selected_sprint = _get_sprint_or_404(sprint_id)
        if str(selected_sprint.get("project_id")) != str(project_object_id):
            raise HTTPException(
                status_code=400,
                detail="Sprint belongs to a different project",
            )
        selected_sprint_id = str(selected_sprint["_id"])
    elif not backlog:
        selected_sprint = sprints_collection.find_one(
            {"project_id": project_object_id, "status": SprintStatus.ACTIVE.value}
        )
        if selected_sprint:
            selected_sprint_id = str(selected_sprint["_id"])

    tickets = list(
        ticket_collection.find(
            {
                "project_id": str(project_object_id),
                "sprint_id": selected_sprint_id,
            }
        )
    )
    statuses_by_id = {
        str(status["_id"]): status
        for status in statuses_collection.find(
            {
                "_id": {
                    "$in": [
                        parse_object_id(status_id, "status_id")
                        for status_id in project.get("assigned_statuses", [])
                    ]
                }
            }
        )
    }
    columns = []
    for status_id in map(str, project.get("assigned_statuses", [])):
        status_record = statuses_by_id.get(status_id, {})
        column_tickets = _sorted_tickets(
            [
                ticket
                for ticket in tickets
                if str(ticket.get("current_status_id")) == status_id
            ],
            "board_position",
        )
        columns.append(
            {
                "status_id": status_id,
                "status_name": status_record.get("name"),
                "tickets": [_serialize_ticket(ticket) for ticket in column_tickets],
            }
        )

    return {
        "project_id": str(project_object_id),
        "scope": "sprint" if selected_sprint else "backlog",
        "sprint": _serialize_sprint(selected_sprint) if selected_sprint else None,
        "columns": columns,
    }


@router.patch("/tickets/{ticket_id}/sprint")
def set_ticket_sprint(
    ticket_id: str,
    payload: TicketSprintUpdate,
    current_user: dict = Depends(get_current_user),
):
    ticket_object_id, ticket = _get_ticket_or_404(ticket_id)
    project_object_id, _ = _get_project_or_404(ticket.get("project_id"))
    _require_project_work(project_object_id, current_user)

    old_sprint_id = ticket.get("sprint_id")
    if old_sprint_id:
        _, old_sprint = _get_sprint_or_404(old_sprint_id)
        _ensure_sprint_is_mutable(old_sprint)

    target_sprint_id = None
    if payload.sprint_id is not None:
        _, target_sprint = _get_sprint_or_404(payload.sprint_id)
        if str(target_sprint.get("project_id")) != str(project_object_id):
            raise HTTPException(
                status_code=400,
                detail="Sprint belongs to a different project",
            )
        _ensure_sprint_is_mutable(target_sprint)
        target_sprint_id = str(target_sprint["_id"])

    if old_sprint_id == target_sprint_id:
        return _serialize_ticket(ticket)

    status_id = str(ticket.get("current_status_id"))
    now = datetime.now(timezone.utc)
    update = {
        "sprint_id": target_sprint_id,
        "board_position": _next_board_position(
            project_object_id,
            status_id,
            target_sprint_id,
        ),
        "updated_at": now,
        "updated_by": str(current_user["_id"]),
    }
    if target_sprint_id is None:
        update["backlog_position"] = _next_backlog_position(project_object_id)
    else:
        update["backlog_position"] = None
    ticket_collection.update_one({"_id": ticket_object_id}, {"$set": update})

    if old_sprint_id is None:
        remaining_backlog = _sorted_tickets(
            [
                item
                for item in ticket_collection.find(
                    {"project_id": str(project_object_id), "sprint_id": None}
                )
                if item.get("_id") != ticket_object_id
            ],
            "backlog_position",
        )
        _renumber_tickets(remaining_backlog, "backlog_position", current_user)
    old_column = _sorted_tickets(
        [
            item
            for item in ticket_collection.find(
                {
                    "project_id": str(project_object_id),
                    "sprint_id": old_sprint_id,
                    "current_status_id": status_id,
                }
            )
            if item.get("_id") != ticket_object_id
        ],
        "board_position",
    )
    _renumber_tickets(old_column, "board_position", current_user)

    log_activity(
        ticket_id=str(ticket_object_id),
        action="ticket_sprint_changed",
        changed_by=current_user["_id"],
        changes=[
            {
                "field": "sprint_id",
                "old_value": old_sprint_id,
                "new_value": target_sprint_id,
            }
        ],
    )
    ticket.update(update)
    return _serialize_ticket(ticket)


@router.patch("/tickets/{ticket_id}/story-points")
def set_story_points(
    ticket_id: str,
    payload: StoryPointsUpdate,
    current_user: dict = Depends(get_current_user),
):
    ticket_object_id, ticket = _get_ticket_or_404(ticket_id)
    project_object_id, _ = _get_project_or_404(ticket.get("project_id"))
    _require_project_work(project_object_id, current_user)
    if ticket.get("sprint_id"):
        _, sprint = _get_sprint_or_404(ticket["sprint_id"])
        _ensure_sprint_is_mutable(sprint)

    now = datetime.now(timezone.utc)
    update = {
        "story_points": payload.story_points,
        "updated_at": now,
        "updated_by": str(current_user["_id"]),
    }
    ticket_collection.update_one({"_id": ticket_object_id}, {"$set": update})
    log_activity(
        ticket_id=str(ticket_object_id),
        action="ticket_estimated",
        changed_by=current_user["_id"],
        changes=[
            {
                "field": "story_points",
                "old_value": ticket.get("story_points"),
                "new_value": payload.story_points,
            }
        ],
    )
    ticket.update(update)
    return _serialize_ticket(ticket)


@router.patch("/tickets/{ticket_id}/backlog-position")
def reorder_backlog_ticket(
    ticket_id: str,
    payload: TicketPositionUpdate,
    current_user: dict = Depends(get_current_user),
):
    ticket_object_id, ticket = _get_ticket_or_404(ticket_id)
    project_object_id, _ = _get_project_or_404(ticket.get("project_id"))
    _require_project_work(project_object_id, current_user)
    if ticket.get("sprint_id") is not None:
        raise HTTPException(
            status_code=409,
            detail="Only backlog tickets have a backlog position",
        )

    tickets = list(
        ticket_collection.find(
            {"project_id": str(project_object_id), "sprint_id": None}
        )
    )
    ordered = _move_ticket_in_order(
        tickets,
        ticket_object_id,
        payload.position,
        "backlog_position",
        current_user,
    )
    return {
        "ticket_id": str(ticket_object_id),
        "position": next(
            index
            for index, item in enumerate(ordered)
            if item.get("_id") == ticket_object_id
        ),
        "ordered_ticket_ids": [str(item["_id"]) for item in ordered],
    }


@router.patch("/tickets/{ticket_id}/board-position")
def reorder_board_ticket(
    ticket_id: str,
    payload: TicketPositionUpdate,
    current_user: dict = Depends(get_current_user),
):
    ticket_object_id, ticket = _get_ticket_or_404(ticket_id)
    project_object_id, _ = _get_project_or_404(ticket.get("project_id"))
    _require_project_work(project_object_id, current_user)
    if ticket.get("sprint_id"):
        _, sprint = _get_sprint_or_404(ticket["sprint_id"])
        _ensure_sprint_is_mutable(sprint)

    tickets = list(
        ticket_collection.find(
            {
                "project_id": str(project_object_id),
                "sprint_id": ticket.get("sprint_id"),
                "current_status_id": ticket.get("current_status_id"),
            }
        )
    )
    ordered = _move_ticket_in_order(
        tickets,
        ticket_object_id,
        payload.position,
        "board_position",
        current_user,
    )
    return {
        "ticket_id": str(ticket_object_id),
        "position": next(
            index
            for index, item in enumerate(ordered)
            if item.get("_id") == ticket_object_id
        ),
        "ordered_ticket_ids": [str(item["_id"]) for item in ordered],
    }
