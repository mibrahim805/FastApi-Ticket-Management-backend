import re
from datetime import datetime, timedelta, timezone
from typing import Annotated, List, Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.api.activity import log_activity
from app.api.auth import get_current_user
from app.api.notification_delivery import create_notification
from app.api.permissions import (
    Capability,
    get_accessible_project_ids,
    get_project_membership,
    has_capability,
    require_project_membership,
    require_ticket_membership,
)
from app.api.watcher_notifications import notify_ticket_watchers
from app.db.db import (
    comments_collection,
    counters_collection,
    issue_links_collection,
    notification_preferences_collection,
    notifications_collection,
    project_members_collection,
    projects_collection,
    sprints_collection,
    statuses_collection,
    ticket_collection,
    transitions_collection,
    user_collection,
)
from app.models.project_member import ProjectRole
from app.models.ticket import (
    AssignAgent,
    BulkAssignRequest,
    BulkTransitionRequest,
    IssueLinkCreate,
    IssueType,
    TicketCreate,
    TicketUpdate,
)

router = APIRouter()

DONE_STATUS_NAMES = frozenset({"done", "closed", "resolved"})


def normalize_status_name(name: str) -> str:
    return re.sub(r"[\s-]+", "_", name.strip().lower())


def _parse_object_id(value: str, field_name: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}")


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _serialize_ticket(ticket: dict) -> dict:
    serialized = dict(ticket)
    if isinstance(serialized.get("_id"), ObjectId):
        serialized["_id"] = str(serialized["_id"])
    serialized.setdefault("issue_type", IssueType.TASK.value)
    serialized.setdefault("epic_id", None)
    serialized.setdefault("labels", [])
    serialized.setdefault("watchers", [])
    serialized.setdefault("sprint_id", None)
    serialized.setdefault("story_points", None)
    serialized.setdefault("backlog_position", None)
    serialized.setdefault("board_position", 0)
    return serialized


def _serialize_issue_link(link: dict) -> dict:
    serialized = dict(link)
    if isinstance(serialized.get("_id"), ObjectId):
        serialized["_id"] = str(serialized["_id"])
    return serialized


def _serialize_transition(transition: dict) -> dict:
    serialized = dict(transition)
    if isinstance(serialized.get("_id"), ObjectId):
        serialized["_id"] = str(serialized["_id"])
    if isinstance(serialized.get("project_id"), ObjectId):
        serialized["project_id"] = str(serialized["project_id"])
    return serialized


def _get_ticket_or_404(ticket_id: str) -> tuple[ObjectId, dict]:
    ticket_object_id = _parse_object_id(ticket_id, "ticket_id")
    ticket = ticket_collection.find_one({"_id": ticket_object_id})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket_object_id, ticket


def _get_epic_or_404(epic_id: str) -> tuple[ObjectId, dict]:
    epic_object_id = _parse_object_id(epic_id, "epic_id")
    epic = ticket_collection.find_one({"_id": epic_object_id})
    if not epic:
        raise HTTPException(status_code=404, detail="Epic not found")
    if epic.get("issue_type") != IssueType.EPIC.value:
        raise HTTPException(status_code=400, detail="Referenced issue is not an epic")
    return epic_object_id, epic


def _is_done_issue(issue: dict, terminal_status_id: Optional[str]) -> bool:
    if terminal_status_id and str(issue.get("current_status_id")) == str(
        terminal_status_id
    ):
        return True
    if issue.get("resolved_date") is not None or issue.get("closed_date") is not None:
        return True

    status_name = str(issue.get("current_status_name", "")).strip().lower()
    normalized_status_name = re.sub(r"[\s-]+", "_", status_name)
    return normalized_status_name in DONE_STATUS_NAMES


def _get_ticket_project(ticket: dict) -> tuple[ObjectId, dict]:
    project_object_id = _parse_object_id(ticket.get("project_id"), "project_id")
    project = projects_collection.find_one({"_id": project_object_id})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project_object_id, project


def _next_ticket_position(query: dict, field: str) -> int:
    existing_tickets = list(ticket_collection.find(query))
    positions = [
        item.get(field)
        for item in existing_tickets
        if isinstance(item.get(field), int)
        and not isinstance(item.get(field), bool)
        and item.get(field) >= 0
    ]
    return max(max(positions, default=-1) + 1, len(existing_tickets))


def _outgoing_transitions(project: dict, current_status_id: str) -> list[dict]:
    return [
        transition
        for transition in transitions_collection.find({"project_id": project["_id"]})
        if str(current_status_id)
        in {str(status_id) for status_id in transition.get("from_status_ids", [])}
    ]


def _transition_guard_error(
    transition: dict,
    ticket: dict,
    membership: dict,
) -> tuple[int, str] | None:
    guard = transition.get("guard", {})
    allowed_roles = {
        role.value if isinstance(role, ProjectRole) else role
        for role in guard.get(
            "allowed_roles",
            [ProjectRole.LEAD.value, ProjectRole.MEMBER.value],
        )
    }
    if membership.get("role") not in allowed_roles:
        return 403, "Your project role cannot use this transition"
    if guard.get("requires_assignee") and not ticket.get("assigned_agent"):
        return 400, "Ticket must be assigned before using this transition"
    return None


def _apply_ticket_transition(
    *,
    ticket_object_id: ObjectId,
    ticket: dict,
    project: dict,
    transition: dict,
    current_user: dict,
) -> dict:
    membership = require_ticket_membership(
        ticket,
        current_user,
        project_members_collection,
        capability=Capability.TRANSITION_TICKETS,
    )
    transition_project_id = transition.get("project_id")
    if transition_project_id is not None and str(transition_project_id) != str(
        project["_id"]
    ):
        raise HTTPException(
            status_code=400,
            detail="Transition belongs to a different project",
        )

    allowed_sources = {
        str(status_id) for status_id in transition.get("from_status_ids", [])
    }
    if str(ticket.get("current_status_id")) not in allowed_sources:
        raise HTTPException(
            status_code=400,
            detail="Ticket's current status is not an allowed transition source",
        )

    guard_error = _transition_guard_error(transition, ticket, membership)
    if guard_error:
        status_code, detail = guard_error
        raise HTTPException(status_code=status_code, detail=detail)

    target_status_id = str(transition.get("to_status_id"))
    assigned_statuses = [
        str(status_id) for status_id in project.get("assigned_statuses", [])
    ]
    if target_status_id not in assigned_statuses:
        raise HTTPException(
            status_code=400,
            detail="Transition target status is not assigned to this project",
        )
    target_status = statuses_collection.find_one(
        {"_id": _parse_object_id(target_status_id, "status_id")}
    )
    if not target_status:
        raise HTTPException(
            status_code=404,
            detail="Transition target status not found",
        )

    now = datetime.now(timezone.utc)
    target_index = assigned_statuses.index(target_status_id)
    is_terminal = target_index == len(assigned_statuses) - 1
    is_resolved = (
        len(assigned_statuses) >= 3 and target_index == len(assigned_statuses) - 2
    )
    normalized_target = normalize_status_name(target_status["name"])
    if normalized_target in {"done", "closed"}:
        is_terminal = True
    if normalized_target == "resolved":
        is_resolved = True

    update_fields = {
        "current_status_id": target_status_id,
        "current_status_name": target_status["name"],
        "updated_at": now,
        "updated_by": str(current_user["_id"]),
        "closed_date": ((ticket.get("closed_date") or now) if is_terminal else None),
        "resolved_date": (
            (ticket.get("resolved_date") or now) if is_terminal or is_resolved else None
        ),
        "board_position": _next_ticket_position(
            {
                "project_id": str(project["_id"]),
                "sprint_id": ticket.get("sprint_id"),
                "current_status_id": target_status_id,
            },
            "board_position",
        ),
    }
    update_result = ticket_collection.update_one(
        {
            "_id": ticket_object_id,
            "current_status_id": ticket.get("current_status_id"),
        },
        {"$set": update_fields},
    )
    if update_result is not None and update_result.matched_count == 0:
        raise HTTPException(
            status_code=409,
            detail="Ticket status changed before the transition could be applied",
        )
    create_notification(
        project_id=ticket.get("project_id"),
        ticket_id=ticket_object_id,
        user_id=current_user["_id"],
        actor_id=current_user["_id"],
        event_type="ticket_transitioned",
        notifications_collection=notifications_collection,
        preferences_collection=notification_preferences_collection,
    )
    notify_ticket_watchers(
        ticket=ticket,
        ticket_id=str(ticket_object_id),
        event_type="ticket_transitioned",
        actor_id=current_user["_id"],
        notifications_collection=notifications_collection,
        preferences_collection=notification_preferences_collection,
    )
    log_activity(
        ticket_id=str(ticket_object_id),
        action="ticket_transitioned",
        changed_by=current_user["_id"],
        changes=[
            {
                "field": "current_status_id",
                "old_value": ticket.get("current_status_id"),
                "new_value": target_status_id,
            },
            {
                "field": "current_status_name",
                "old_value": ticket.get("current_status_name"),
                "new_value": target_status["name"],
            },
        ],
    )
    return {
        "ticket_id": str(ticket_object_id),
        "ticket_code": ticket.get("ticket_code"),
        "transition_id": str(transition.get("_id")),
        "transition": transition.get("name"),
        "previous_status": ticket.get("current_status_name"),
        "current_status": target_status["name"],
        "updated_at": now,
    }


def _run_transition_by_id(
    ticket_id: str,
    transition_id: str,
    current_user: dict,
) -> dict:
    ticket_object_id, ticket = _get_ticket_or_404(ticket_id)
    require_ticket_membership(
        ticket,
        current_user,
        project_members_collection,
    )
    _, project = _get_ticket_project(ticket)
    transition_object_id = _parse_object_id(transition_id, "transition_id")
    transition = transitions_collection.find_one({"_id": transition_object_id})
    if not transition:
        raise HTTPException(status_code=404, detail="Transition not found")
    return _apply_ticket_transition(
        ticket_object_id=ticket_object_id,
        ticket=ticket,
        project=project,
        transition=transition,
        current_user=current_user,
    )


def _initial_project_ticket_counter(project: dict) -> int:

    highest_number = 0
    ticket_code_pattern = re.compile(
        rf"^{re.escape(project['code'])}-(\d+)$",
        re.IGNORECASE,
    )
    for existing_ticket in ticket_collection.find({"project_id": str(project["_id"])}):
        match = ticket_code_pattern.fullmatch(existing_ticket.get("ticket_code", ""))
        if match:
            highest_number = max(highest_number, int(match.group(1)))
    return highest_number


def _next_project_ticket_number(project: dict) -> int:
    counter_id = f"ticket_counter:{project['_id']}"
    if not counters_collection.find_one({"_id": counter_id}):
        try:
            counters_collection.insert_one(
                {
                    "_id": counter_id,
                    "counter": _initial_project_ticket_counter(project),
                }
            )
        except DuplicateKeyError:
            pass

    return counters_collection.find_one_and_update(
        {"_id": counter_id},
        {"$inc": {"counter": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )["counter"]


def calculate_due_date(priority: str) -> datetime:
    now = datetime.now(timezone.utc)

    sla_windows = {
        "urgent": timedelta(hours=4),
        "high": timedelta(days=1),
        "medium": timedelta(days=3),
        "low": timedelta(days=7),
    }
    try:
        return now + sla_windows[priority.lower()]
    except KeyError:
        raise ValueError(f"Invalid priority: {priority}")


@router.post("/", tags=["Tickets"])
def create_ticket(
    payload: TicketCreate,
    current_user: dict = Depends(get_current_user),
):

    project = projects_collection.find_one(
        {"_id": _parse_object_id(payload.project_id, "project_id")}
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    require_project_membership(
        project["_id"],
        current_user,
        project_members_collection,
    )

    epic_id = None
    if payload.epic_id is not None:
        if payload.issue_type == IssueType.EPIC:
            raise HTTPException(
                status_code=400,
                detail="An epic cannot belong to another epic",
            )
        epic_object_id, epic = _get_epic_or_404(payload.epic_id)
        if str(epic.get("project_id")) != str(project["_id"]):
            raise HTTPException(
                status_code=400,
                detail="Epic must belong to the same project as the issue",
            )
        epic_id = str(epic_object_id)

    assigned_statuses = project.get("assigned_statuses", [])
    if not assigned_statuses:
        raise HTTPException(
            status_code=400,
            detail="Project has no statuses assigned. Contact an administrator.",
        )

    first_status_id = assigned_statuses[0]
    first_status = statuses_collection.find_one(
        {"_id": _parse_object_id(first_status_id, "status_id")}
    )
    if not first_status:
        raise HTTPException(
            status_code=400,
            detail="Project's first status was not found. Contact an administrator.",
        )

    project_key = project.get("code")
    if not project_key:
        raise HTTPException(
            status_code=400,
            detail="Project is missing a project key. Contact an administrator.",
        )
    next_number = _next_project_ticket_number(project)
    ticket_code = f"{project_key}-{next_number}"

    ticket = {
        "project_id": str(project["_id"]),
        "ticket_code": ticket_code,
        "project_key": project.get("code"),
        "userId": str(current_user["_id"]),
        "title": payload.title,
        "description": payload.description,
        "category": payload.category.value,
        "issue_type": payload.issue_type.value,
        "epic_id": epic_id,
        "labels": list(payload.labels),
        "watchers": [],
        "sprint_id": None,
        "story_points": payload.story_points,
        "backlog_position": _next_ticket_position(
            {"project_id": str(project["_id"]), "sprint_id": None},
            "backlog_position",
        ),
        "board_position": _next_ticket_position(
            {
                "project_id": str(project["_id"]),
                "sprint_id": None,
                "current_status_id": str(first_status_id),
            },
            "board_position",
        ),
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

    notification_record = create_notification(
        project_id=ticket["project_id"],
        ticket_id=ticket["_id"],
        user_id=str(current_user["_id"]),
        actor_id=current_user["_id"],
        event_type="ticket_created",
        notifications_collection=notifications_collection,
        preferences_collection=notification_preferences_collection,
    )
    if notification_record is not None:
        ticket["notification_id"] = str(notification_record.inserted_id)

    return _serialize_ticket(ticket)


@router.get("/epics/{epic_id}", tags=["Epics"])
def get_epic(
    epic_id: str,
    current_user: dict = Depends(get_current_user),
):
    epic_object_id, epic = _get_epic_or_404(epic_id)
    require_ticket_membership(
        epic,
        current_user,
        project_members_collection,
    )

    project_object_id = _parse_object_id(epic.get("project_id"), "project_id")
    project = projects_collection.find_one({"_id": project_object_id})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    issues = list(
        ticket_collection.find(
            {
                "epic_id": str(epic_object_id),
                "project_id": str(project_object_id),
            }
        )
    )
    assigned_statuses = project.get("assigned_statuses", [])
    terminal_status_id = assigned_statuses[-1] if assigned_statuses else None
    done_count = sum(_is_done_issue(issue, terminal_status_id) for issue in issues)

    return {
        "epic": _serialize_ticket(epic),
        "issues": [_serialize_ticket(issue) for issue in issues],
        "progress": {
            "done": done_count,
            "total": len(issues),
        },
    }


@router.get("/tickets/detail", tags=["Tickets"])
def get_ticket_details(current_user: dict = Depends(get_current_user), ticket_id=None):
    ticket = ticket_collection.find_one(
        {"_id": _parse_object_id(ticket_id, "ticket_id")}
    )
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    membership = require_ticket_membership(
        ticket,
        current_user,
        project_members_collection,
    )

    ticket["_id"] = str(ticket["_id"])
    comment_query = {"ticket_id": ticket_id}
    if not has_capability(membership, Capability.VIEW_INTERNAL_COMMENTS):
        comment_query["visibility"] = "public"
    comments = list(comments_collection.find(comment_query))
    for comment in comments:
        comment["_id"] = str(comment["_id"])

    return {
        "tickets": _serialize_ticket(ticket),
        "comments": comments,
    }


@router.post("/tickets/{ticket_id}/watchers/subscribe", tags=["Tickets"])
def subscribe_to_ticket(
    ticket_id: str,
    current_user: dict = Depends(get_current_user),
):
    ticket_object_id, ticket = _get_ticket_or_404(ticket_id)
    require_ticket_membership(
        ticket,
        current_user,
        project_members_collection,
    )
    watcher_id = str(_parse_object_id(current_user.get("_id"), "user_id"))
    ticket_collection.update_one(
        {"_id": ticket_object_id},
        {"$addToSet": {"watchers": watcher_id}},
    )
    updated_ticket = ticket_collection.find_one({"_id": ticket_object_id})
    old_watchers = ticket.get("watchers", [])
    new_watchers = updated_ticket.get("watchers", [])
    if old_watchers != new_watchers:
        log_activity(
            ticket_id=ticket_id,
            action="watcher_subscribed",
            changed_by=current_user["_id"],
            changes=[
                {
                    "field": "watchers",
                    "old_value": old_watchers,
                    "new_value": new_watchers,
                }
            ],
        )
    return {
        "message": "Subscribed to ticket",
        "watchers": new_watchers,
    }


@router.delete("/tickets/{ticket_id}/watchers/unsubscribe", tags=["Tickets"])
def unsubscribe_from_ticket(
    ticket_id: str,
    current_user: dict = Depends(get_current_user),
):
    ticket_object_id, ticket = _get_ticket_or_404(ticket_id)
    require_ticket_membership(
        ticket,
        current_user,
        project_members_collection,
    )
    watcher_id = str(_parse_object_id(current_user.get("_id"), "user_id"))
    ticket_collection.update_one(
        {"_id": ticket_object_id},
        {"$pull": {"watchers": watcher_id}},
    )
    updated_ticket = ticket_collection.find_one({"_id": ticket_object_id})
    old_watchers = ticket.get("watchers", [])
    new_watchers = updated_ticket.get("watchers", [])
    if old_watchers != new_watchers:
        log_activity(
            ticket_id=ticket_id,
            action="watcher_unsubscribed",
            changed_by=current_user["_id"],
            changes=[
                {
                    "field": "watchers",
                    "old_value": old_watchers,
                    "new_value": new_watchers,
                }
            ],
        )
    return {
        "message": "Unsubscribed from ticket",
        "watchers": new_watchers,
    }


@router.get("/tickets/{ticket_id}/transitions", tags=["Tickets"])
def get_ticket_transitions(
    ticket_id: str,
    current_user: dict = Depends(get_current_user),
):
    _, ticket = _get_ticket_or_404(ticket_id)
    membership = require_ticket_membership(
        ticket,
        current_user,
        project_members_collection,
    )
    _, project = _get_ticket_project(ticket)
    transitions = []
    for transition in _outgoing_transitions(
        project,
        ticket.get("current_status_id"),
    ):
        target_status = statuses_collection.find_one(
            {
                "_id": _parse_object_id(
                    transition.get("to_status_id"),
                    "status_id",
                )
            }
        )
        guard_error = _transition_guard_error(transition, ticket, membership)
        transitions.append(
            {
                **_serialize_transition(transition),
                "to_status_name": target_status.get("name") if target_status else None,
                "available": guard_error is None and target_status is not None,
                "blocked_reason": guard_error[1] if guard_error else None,
            }
        )
    return {"transitions": transitions}


@router.post("/{ticket_id}/transitions/{transition_id}", tags=["Tickets"])
def transition_ticket(
    ticket_id: str,
    transition_id: str,
    current_user: dict = Depends(get_current_user),
):
    return _run_transition_by_id(
        ticket_id,
        transition_id,
        current_user,
    )


@router.post("/tickets/{ticket_id}/links", status_code=201, tags=["Tickets"])
def create_ticket_link(
    ticket_id: str,
    payload: IssueLinkCreate,
    current_user: dict = Depends(get_current_user),
):
    from_ticket_object_id, from_ticket = _get_ticket_or_404(ticket_id)
    require_ticket_membership(
        from_ticket,
        current_user,
        project_members_collection,
        capability=Capability.LINK_TICKETS,
        role_detail="Only project leads or members can link tickets",
    )

    to_ticket_object_id = _parse_object_id(payload.to_ticket_id, "to_ticket_id")
    if to_ticket_object_id == from_ticket_object_id:
        raise HTTPException(status_code=400, detail="A ticket cannot link to itself")

    to_ticket = ticket_collection.find_one({"_id": to_ticket_object_id})
    if not to_ticket:
        raise HTTPException(status_code=404, detail="Target ticket not found")
    require_ticket_membership(
        to_ticket,
        current_user,
        project_members_collection,
        capability=Capability.LINK_TICKETS,
        role_detail="Only project leads or members can link tickets",
    )

    link = {
        "from_ticket_id": str(from_ticket_object_id),
        "to_ticket_id": str(to_ticket_object_id),
        "link_type": payload.link_type.value,
    }
    if issue_links_collection.find_one(link):
        raise HTTPException(status_code=409, detail="Issue link already exists")

    try:
        result = issue_links_collection.insert_one(link)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Issue link already exists")

    link["_id"] = result.inserted_id
    log_activity(
        ticket_id=str(from_ticket_object_id),
        action="issue_link_created",
        changed_by=current_user["_id"],
        changes=[
            {
                "field": "outgoing_issue_link",
                "old_value": None,
                "new_value": {
                    "link_id": str(result.inserted_id),
                    "ticket_id": str(to_ticket_object_id),
                    "link_type": payload.link_type.value,
                },
            }
        ],
    )
    log_activity(
        ticket_id=str(to_ticket_object_id),
        action="issue_link_created",
        changed_by=current_user["_id"],
        changes=[
            {
                "field": "incoming_issue_link",
                "old_value": None,
                "new_value": {
                    "link_id": str(result.inserted_id),
                    "ticket_id": str(from_ticket_object_id),
                    "link_type": payload.link_type.value,
                },
            }
        ],
    )
    notify_ticket_watchers(
        ticket=from_ticket,
        ticket_id=str(from_ticket_object_id),
        event_type="ticket_linked",
        actor_id=current_user["_id"],
        notifications_collection=notifications_collection,
        preferences_collection=notification_preferences_collection,
    )
    notify_ticket_watchers(
        ticket=to_ticket,
        ticket_id=str(to_ticket_object_id),
        event_type="ticket_linked",
        actor_id=current_user["_id"],
        notifications_collection=notifications_collection,
        preferences_collection=notification_preferences_collection,
    )
    return _serialize_issue_link(link)


@router.get("/tickets/{ticket_id}/links", tags=["Tickets"])
def get_ticket_links(
    ticket_id: str,
    current_user: dict = Depends(get_current_user),
):
    ticket_object_id, ticket = _get_ticket_or_404(ticket_id)
    require_ticket_membership(
        ticket,
        current_user,
        project_members_collection,
    )

    canonical_ticket_id = str(ticket_object_id)
    links = issue_links_collection.find(
        {
            "$or": [
                {"from_ticket_id": canonical_ticket_id},
                {"to_ticket_id": canonical_ticket_id},
            ]
        }
    )
    return {"links": [_serialize_issue_link(link) for link in links]}


@router.put("/update_ticket/{ticket_id}", tags=["Tickets"])
def update_ticket(
    ticket_id: str, data: TicketUpdate, current_user: dict = Depends(get_current_user)
):
    ticket = ticket_collection.find_one(
        {"_id": _parse_object_id(ticket_id, "ticket_id")}
    )
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    require_ticket_membership(
        ticket,
        current_user,
        project_members_collection,
        capability=Capability.UPDATE_TICKET,
        role_detail="Only project leads or members can update tickets",
    )

    update_data = data.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No ticket fields supplied")

    if "story_points" in update_data and ticket.get("sprint_id"):
        sprint = sprints_collection.find_one(
            {"_id": _parse_object_id(ticket["sprint_id"], "sprint_id")}
        )
        if sprint and sprint.get("status") == "completed":
            raise HTTPException(
                status_code=409,
                detail="Completed sprint tickets and estimates are locked",
            )

    changes = [
        {
            "field": field,
            "old_value": ticket.get(field),
            "new_value": value,
        }
        for field, value in update_data.items()
        if ticket.get(field) != value
    ]
    update_data["updated_by"] = str(current_user["_id"])
    update_data["updated_at"] = datetime.now(timezone.utc)
    ticket_collection.update_one(
        {"_id": _parse_object_id(ticket_id, "ticket_id")}, {"$set": update_data}
    )

    create_notification(
        project_id=ticket.get("project_id"),
        ticket_id=ticket_id,
        user_id=str(current_user["_id"]),
        actor_id=current_user["_id"],
        event_type="ticket_updated",
        notifications_collection=notifications_collection,
        preferences_collection=notification_preferences_collection,
    )
    notify_ticket_watchers(
        ticket=ticket,
        ticket_id=ticket_id,
        event_type="ticket_updated",
        actor_id=current_user["_id"],
        notifications_collection=notifications_collection,
        preferences_collection=notification_preferences_collection,
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

    actor_membership = require_ticket_membership(
        ticket,
        current_user,
        project_members_collection,
        capability=Capability.ASSIGN_TICKETS,
        role_detail="Only project leads or members can assign tickets",
    )

    agent_object_id = _parse_object_id(data.agent_id, "agent_id")
    agent = user_collection.find_one({"_id": agent_object_id})
    if not agent:
        raise HTTPException(status_code=404, detail="User not found")

    target_membership = get_project_membership(
        ticket["project_id"],
        agent_object_id,
        project_members_collection,
    )
    if not target_membership or not has_capability(
        target_membership, Capability.ASSIGN_TICKETS
    ):
        raise HTTPException(
            status_code=400,
            detail="Target user must be a lead or member of this project",
        )

    current_user_id = str(current_user["_id"])
    is_lead = actor_membership.get("role") == ProjectRole.LEAD.value
    is_self_assign = current_user_id == str(agent_object_id)
    is_reassigning_another_member = ticket.get("assigned_agent") not in (
        None,
        current_user_id,
    )
    if not is_lead and (not is_self_assign or is_reassigning_another_member):
        raise HTTPException(
            status_code=403,
            detail="Project members can only self-assign unassigned tickets",
        )

    ticket_collection.update_one(
        {"_id": _parse_object_id(ticket_id, "ticket_id")},
        {
            "$set": {
                "assigned_agent": data.agent_id,
                "updated_by": str(current_user["_id"]),
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )

    log_activity(
        ticket_id=ticket_id,
        action="ticket_assigned",
        changed_by=current_user["_id"],
        changes=[
            {
                "field": "assigned_agent",
                "old_value": ticket.get("assigned_agent"),
                "new_value": data.agent_id,
            }
        ],
    )

    create_notification(
        project_id=ticket.get("project_id"),
        ticket_id=ticket_id,
        user_id=str(current_user["_id"]),
        actor_id=current_user["_id"],
        event_type="ticket_assigned",
        notifications_collection=notifications_collection,
        preferences_collection=notification_preferences_collection,
    )
    notify_ticket_watchers(
        ticket=ticket,
        ticket_id=ticket_id,
        event_type="ticket_assigned",
        actor_id=current_user["_id"],
        notifications_collection=notifications_collection,
        preferences_collection=notification_preferences_collection,
    )

    return {"message": "Ticket assigned successfully"}


@router.post("/tickets/bulk/assign", tags=["Tickets"])
def bulk_assign_tickets(
    payload: BulkAssignRequest,
    current_user: dict = Depends(get_current_user),
):
    succeeded = []
    failed = []
    assignment = AssignAgent(agent_id=payload.agent_id)
    for ticket_id in payload.ticket_ids:
        try:
            assign_ticket(ticket_id, assignment, current_user)
        except HTTPException as error:
            failed.append(
                {
                    "ticket_id": ticket_id,
                    "status_code": error.status_code,
                    "detail": error.detail,
                }
            )
        else:
            succeeded.append({"ticket_id": ticket_id})

    return {
        "succeeded": succeeded,
        "failed": failed,
        "total": len(payload.ticket_ids),
    }


@router.post("/tickets/bulk/transition", tags=["Tickets"])
def bulk_transition_tickets(
    payload: BulkTransitionRequest,
    current_user: dict = Depends(get_current_user),
):
    succeeded = []
    failed = []
    for ticket_id in payload.ticket_ids:
        try:
            result = _run_transition_by_id(
                ticket_id,
                payload.transition_id,
                current_user,
            )
        except HTTPException as error:
            failed.append(
                {
                    "ticket_id": ticket_id,
                    "status_code": error.status_code,
                    "detail": error.detail,
                }
            )
        else:
            succeeded.append(result)

    return {
        "succeeded": succeeded,
        "failed": failed,
        "total": len(payload.ticket_ids),
    }


def _build_ticket_list_query(
    *,
    project_ids: List[str],
    status: Optional[str],
    priority: Optional[str],
    category: Optional[str],
    issue_type: Optional[IssueType],
    labels: Optional[List[str]],
    epic_id: Optional[str],
    assigned_agent: Optional[str],
    search: Optional[str],
    overdue_only: Optional[bool],
) -> dict:
    if len(project_ids) == 1:
        query = {"project_id": project_ids[0]}
    else:
        query = {"project_id": {"$in": project_ids}}

    if status:
        query["status"] = status.lower()
    if priority:
        query["priority"] = priority.lower()
    if category:
        query["category"] = category
    if issue_type:
        if issue_type == IssueType.TASK:
            query["issue_type"] = {"$in": [IssueType.TASK.value, None]}
        else:
            query["issue_type"] = issue_type.value
    if labels:
        query["labels"] = {"$all": list(dict.fromkeys(labels))}
    if epic_id:
        query["epic_id"] = str(_parse_object_id(epic_id, "epic_id"))
    if assigned_agent:
        query["assigned_agent"] = assigned_agent
    if overdue_only:
        query["$expr"] = {
            "$and": [
                {"$lt": ["$due_date", datetime.now(timezone.utc)]},
                {"$nin": ["$current_status_name", ["resolved", "closed"]]},
            ]
        }
    if search:
        search_regex = re.compile(re.escape(search), re.IGNORECASE)
        query["$or"] = [
            {"title": {"$regex": search_regex}},
            {"ticket_code": {"$regex": search_regex}},
            {"description": {"$regex": search_regex}},
        ]
    return query


def _paginate_ticket_query(
    *,
    query: dict,
    page: int,
    page_size: int,
    sort_by: Optional[str],
    sort_order: Optional[str],
) -> tuple[list[dict], dict]:
    sort_field_map = {
        "created_at": "created_date",
        "priority": "priority",
        "due_date": "due_date",
    }
    mongo_sort_field = sort_field_map.get(sort_by, "created_date")
    mongo_sort_dir = 1 if sort_order == "asc" else -1
    total_count = ticket_collection.count_documents(query)
    cursor = (
        ticket_collection.find(query)
        .sort(mongo_sort_field, mongo_sort_dir)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    tickets = []
    now = datetime.now(timezone.utc)
    for ticket in cursor:
        if ticket.get("due_date") and ticket.get("current_status_name") not in [
            "resolved",
            "closed",
        ]:
            ticket["is_overdue"] = _as_aware_utc(ticket["due_date"]) < now
        else:
            ticket["is_overdue"] = False
        tickets.append(_serialize_ticket(ticket))

    total_pages = (total_count + page_size - 1) // page_size
    return tickets, {
        "total": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
    }


def _ticket_filters_applied(
    *,
    status: Optional[str],
    priority: Optional[str],
    category: Optional[str],
    issue_type: Optional[IssueType],
    labels: Optional[List[str]],
    epic_id: Optional[str],
    assigned_agent: Optional[str],
    search: Optional[str],
    overdue_only: Optional[bool],
    sort_by: Optional[str],
    sort_order: Optional[str],
) -> dict:
    return {
        "status": status,
        "priority": priority,
        "category": category,
        "issue_type": issue_type.value if issue_type else None,
        "labels": labels,
        "epic_id": epic_id,
        "assigned_agent": assigned_agent,
        "search": search,
        "overdue_only": overdue_only,
        "sort_by": sort_by,
        "sort_order": sort_order,
    }


@router.get("/tickets/search", tags=["Tickets"])
def search_tickets(
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(10, ge=1, le=100, description="Results per page"),
    status: Optional[str] = Query(
        None, description="Filter by status (e.g. open, closed, pending)"
    ),
    priority: Optional[str] = Query(
        None, description="Filter by priority (e.g. low, medium, high)"
    ),
    category: Optional[str] = Query(None, description="Filter by category"),
    issue_type: Annotated[
        Optional[IssueType],
        Query(description="Filter by issue type"),
    ] = None,
    labels: Annotated[
        Optional[List[str]],
        Query(description="Filter by all supplied labels; repeat for multiple labels"),
    ] = None,
    epic_id: Annotated[
        Optional[str],
        Query(description="Filter by parent epic ID"),
    ] = None,
    assigned_agent: Optional[str] = Query(
        None, description="Filter by assigned agent ID"
    ),
    search: Optional[str] = Query(
        None,
        max_length=200,
        description="Search across title, ticket number, and description",
    ),
    overdue_only: Optional[bool] = Query(
        None, description="Filter to show only overdue tickets"
    ),
    sort_by: Optional[str] = Query(
        "created_at",
        pattern="^(created_at|priority|due_date)$",
        description="Sort field",
    ),
    sort_order: Optional[str] = Query(
        "desc", pattern="^(asc|desc)$", description="Sort direction"
    ),
    current_user: dict = Depends(get_current_user),
):
    project_ids = get_accessible_project_ids(
        current_user,
        project_members_collection,
        projects_collection,
    )
    query = _build_ticket_list_query(
        project_ids=project_ids,
        status=status,
        priority=priority,
        category=category,
        issue_type=issue_type,
        labels=labels,
        epic_id=epic_id,
        assigned_agent=assigned_agent,
        search=search,
        overdue_only=overdue_only,
    )
    tickets, pagination = _paginate_ticket_query(
        query=query,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return {
        "data": tickets,
        "pagination": pagination,
        "scope": "all_accessible_projects",
        "filters_applied": _ticket_filters_applied(
            status=status,
            priority=priority,
            category=category,
            issue_type=issue_type,
            labels=labels,
            epic_id=epic_id,
            assigned_agent=assigned_agent,
            search=search,
            overdue_only=overdue_only,
            sort_by=sort_by,
            sort_order=sort_order,
        ),
    }


@router.get("/tickets/{project_key}", tags=["Tickets"])
def get_tickets(
    project_key: str,
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(10, ge=1, le=100, description="Results per page"),
    status: Optional[str] = Query(
        None, description="Filter by status (e.g. open, closed, pending)"
    ),
    priority: Optional[str] = Query(
        None, description="Filter by priority (e.g. low, medium, high)"
    ),
    category: Optional[str] = Query(None, description="Filter by category"),
    issue_type: Annotated[
        Optional[IssueType],
        Query(description="Filter by issue type"),
    ] = None,
    labels: Annotated[
        Optional[List[str]],
        Query(description="Filter by all supplied labels; repeat for multiple labels"),
    ] = None,
    epic_id: Annotated[
        Optional[str],
        Query(description="Filter by parent epic ID"),
    ] = None,
    assigned_agent: Optional[str] = Query(
        None, description="Filter by assigned agent ID"
    ),
    search: Optional[str] = Query(
        None,
        max_length=200,
        description="Search across title, ticket number, and description",
    ),
    overdue_only: Optional[bool] = Query(
        None, description="Filter to show only overdue tickets"
    ),
    sort_by: Optional[str] = Query(
        "created_at",
        pattern="^(created_at|priority|due_date)$",
        description="Sort field",
    ),
    sort_order: Optional[str] = Query(
        "desc", pattern="^(asc|desc)$", description="Sort direction"
    ),
    current_user: dict = Depends(get_current_user),
):
    project = projects_collection.find_one({"code": project_key})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    require_project_membership(
        project["_id"],
        current_user,
        project_members_collection,
    )
    query = _build_ticket_list_query(
        project_ids=[str(project["_id"])],
        status=status,
        priority=priority,
        category=category,
        issue_type=issue_type,
        labels=labels,
        epic_id=epic_id,
        assigned_agent=assigned_agent,
        search=search,
        overdue_only=overdue_only,
    )
    tickets, pagination = _paginate_ticket_query(
        query=query,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return {
        "data": tickets,
        "pagination": pagination,
        "filters_applied": _ticket_filters_applied(
            status=status,
            priority=priority,
            category=category,
            issue_type=issue_type,
            labels=labels,
            epic_id=epic_id,
            assigned_agent=assigned_agent,
            search=search,
            overdue_only=overdue_only,
            sort_by=sort_by,
            sort_order=sort_order,
        ),
    }


@router.get("/tickets_summary", tags=["Tickets"])
def get_tickets_summary(
    project_id: Annotated[
        Optional[str],
        Query(description="Limit the summary to one accessible project"),
    ] = None,
    current_user: dict = Depends(get_current_user),
):
    if project_id is not None:
        project_object_id = _parse_object_id(project_id, "project_id")
        project = projects_collection.find_one({"_id": project_object_id})
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        require_project_membership(
            project_object_id,
            current_user,
            project_members_collection,
            allow_super_admin=True,
        )
        project_ids = [str(project_object_id)]
    else:
        project_ids = get_accessible_project_ids(
            current_user,
            project_members_collection,
            projects_collection,
        )
    if not project_ids:
        return {}

    query = {"project_id": {"$in": project_ids}}

    pipeline = [
        {"$match": query},
        {
            "$group": {
                "_id": None,
                "total_tickets": {"$sum": 1},
                "to_do_tickets": {
                    "$sum": {
                        "$cond": [{"$eq": ["$current_status_name", "to_do"]}, 1, 0]
                    }
                },
                "in_development_tickets": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$current_status_name", "in_development"]},
                            1,
                            0,
                        ]
                    }
                },
                "in_qa_tickets": {
                    "$sum": {
                        "$cond": [{"$eq": ["$current_status_name", "in_qa"]}, 1, 0]
                    }
                },
                "qa_passed_tickets": {
                    "$sum": {
                        "$cond": [{"$eq": ["$current_status_name", "qa_passed"]}, 1, 0]
                    }
                },
                "deployed_tickets": {
                    "$sum": {
                        "$cond": [{"$eq": ["$current_status_name", "deployed"]}, 1, 0]
                    }
                },
                "closed_tickets": {
                    "$sum": {
                        "$cond": [{"$eq": ["$current_status_name", "closed"]}, 1, 0]
                    }
                },
                "reopened_tickets": {
                    "$sum": {
                        "$cond": [{"$eq": ["$current_status_name", "reopened"]}, 1, 0]
                    }
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
                "urgent_priority_tickets": {
                    "$sum": {"$cond": [{"$eq": ["$priority", "urgent"]}, 1, 0]}
                },
            }
        },
        {"$project": {"_id": 0}},
    ]

    summary = list(ticket_collection.aggregate(pipeline))
    return summary[0] if summary else {}


@router.delete("/tickets/{ticket_id}", tags=["Tickets"])
def delete_ticket(ticket_id: str, current_user: dict = Depends(get_current_user)):
    ticket_object_id = _parse_object_id(ticket_id, "ticket_id")
    ticket = ticket_collection.find_one({"_id": ticket_object_id})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    require_ticket_membership(
        ticket,
        current_user,
        project_members_collection,
        capability=Capability.DELETE_TICKETS,
        role_detail="Only project leads can delete tickets",
    )

    notify_ticket_watchers(
        ticket=ticket,
        ticket_id=ticket_id,
        event_type="ticket_deleted",
        actor_id=current_user["_id"],
        notifications_collection=notifications_collection,
        preferences_collection=notification_preferences_collection,
    )
    log_activity(
        ticket_id=ticket_id,
        action="ticket_deleted",
        changed_by=current_user["_id"],
        changes=[
            {
                "field": "ticket",
                "old_value": ticket.get("ticket_code", ticket_id),
                "new_value": None,
            }
        ],
    )
    ticket_collection.delete_one({"_id": ticket_object_id})
    issue_links_collection.delete_many(
        {
            "$or": [
                {"from_ticket_id": str(ticket_object_id)},
                {"to_ticket_id": str(ticket_object_id)},
            ]
        }
    )

    create_notification(
        project_id=ticket.get("project_id"),
        ticket_id=ticket_id,
        user_id=str(current_user["_id"]),
        actor_id=current_user["_id"],
        event_type="ticket_deleted",
        notifications_collection=notifications_collection,
        preferences_collection=notification_preferences_collection,
    )

    return {"message": "Ticket deleted successfully"}
