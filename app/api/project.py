import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pymongo.errors import DuplicateKeyError

from app.api.auth import get_current_user
from app.api.permissions import (
    PROJECT_LEAD_ROLES,
    PROJECT_MEMBER_ROLES,
    parse_object_id,
    require_project_membership,
)
from app.db.db import (
    project_members_collection,
    projects_collection,
    statuses_collection,
    ticket_collection,
    transitions_collection,
    user_collection,
)
from app.decorator.super_admin import super_admin_required
from app.models.project import (
    ProjectCreate,
    ProjectMemberRole,
    ProjectStatusAssign,
    ProjectUpdate,
)
from app.models.project_member import ProjectMemberCreate
from app.models.transition import ProjectWorkflowUpdate

router = APIRouter(prefix="/projects", tags=["Projects"])


def _get_project_or_404(project_id: str) -> tuple:
    project_object_id = parse_object_id(project_id, "project_id")
    project = projects_collection.find_one({"_id": project_object_id})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project_object_id, project


def _ensure_project_key_available(code: str, project_id=None) -> None:
    existing_project = projects_collection.find_one(
        {
            "code": {
                "$regex": re.compile(f"^{re.escape(code)}$", re.IGNORECASE),
            }
        }
    )
    if existing_project and existing_project.get("_id") != project_id:
        raise HTTPException(status_code=409, detail="Project key already exists")


def _serialize_project(project: dict) -> dict:
    serialized = dict(project)
    serialized["_id"] = str(serialized["_id"])

    # These fields belong to the retired single-assignee/counter design and
    # must not leak back into the public project representation.
    serialized.pop("assigned_agent", None)
    serialized.pop("ticket_counter", None)
    serialized.pop("workflow_transitions", None)
    return serialized


def _serialize_transition(transition: dict) -> dict:
    serialized = dict(transition)
    serialized["_id"] = str(serialized["_id"])
    serialized["project_id"] = str(serialized["project_id"])
    return serialized


def _require_project_admin(project_id, current_user: dict) -> dict:
    return require_project_membership(
        project_id,
        current_user,
        project_members_collection,
        allowed_roles=PROJECT_LEAD_ROLES,
        role_detail="Only project leads or super admins can configure the project",
        allow_super_admin=True,
    )


@router.post("")
@super_admin_required
def create_project(
    project: ProjectCreate,
    current_user: dict = Depends(get_current_user),
):
    current_user_id = parse_object_id(current_user.get("_id"), "user_id")
    _ensure_project_key_available(project.code)

    project_document = project.model_dump()
    project_document.update(
        {
            "created_by": str(current_user_id),
            "created_at": datetime.now(timezone.utc),
            "assigned_statuses": [],
            "default_status_id": None,
        }
    )
    try:
        result = projects_collection.insert_one(project_document)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Project key already exists")

    project_members_collection.insert_one(
        {
            "project_id": result.inserted_id,
            "user_id": current_user_id,
            "role": ProjectMemberRole.LEAD.value,
        }
    )

    return {"id": str(result.inserted_id), "message": "Project created"}


@router.get("")
def get_projects(current_user: dict = Depends(get_current_user)):
    user_id = parse_object_id(current_user.get("_id"), "user_id")

    if current_user.get("role") == "super_admin":
        projects = list(projects_collection.find({}))
    else:
        memberships = list(project_members_collection.find({"user_id": user_id}))
        projects = []
        seen_project_ids = set()
        for membership in memberships:
            if membership.get("role") not in PROJECT_MEMBER_ROLES:
                continue
            project_id = membership.get("project_id")
            if not project_id or project_id in seen_project_ids:
                continue
            project = projects_collection.find_one({"_id": project_id})
            if project:
                projects.append(project)
                seen_project_ids.add(project_id)

    serialized_projects = []
    for project in projects:
        project_id = str(project["_id"])
        serialized = _serialize_project(project)
        serialized["total_tickets"] = ticket_collection.count_documents(
            {"project_id": project_id}
        )
        serialized_projects.append(serialized)

    return {"projects": serialized_projects}


@router.get("/{project_id}")
def get_project(project_id: str, current_user: dict = Depends(get_current_user)):
    project_object_id, project = _get_project_or_404(project_id)
    require_project_membership(
        project_object_id,
        current_user,
        project_members_collection,
        allow_super_admin=True,
    )
    return {"project": _serialize_project(project)}


@router.get("/{project_id}/workflow")
def get_project_workflow(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    project_object_id, _ = _get_project_or_404(project_id)
    require_project_membership(
        project_object_id,
        current_user,
        project_members_collection,
        allow_super_admin=True,
    )
    return {
        "project_id": project_id,
        "transitions": [
            _serialize_transition(transition)
            for transition in transitions_collection.find(
                {"project_id": project_object_id}
            ).sort("order", 1)
        ],
    }


@router.put("/{project_id}/workflow")
def configure_project_workflow(
    project_id: str,
    payload: ProjectWorkflowUpdate,
    current_user: dict = Depends(get_current_user),
):
    project_object_id, project = _get_project_or_404(project_id)
    _require_project_admin(project_object_id, current_user)

    assigned_statuses = {
        str(status_id) for status_id in project.get("assigned_statuses", [])
    }

    transitions = []
    now = datetime.now(timezone.utc)
    for order, transition in enumerate(payload.transitions):
        from_status_ids = [
            str(parse_object_id(status_id, "status_id"))
            for status_id in transition.from_status_ids
        ]
        if len(from_status_ids) != len(set(from_status_ids)):
            raise HTTPException(
                status_code=400,
                detail="Transition source status IDs must be unique",
            )
        to_status_id = str(parse_object_id(transition.to_status_id, "status_id"))
        for from_status_id in from_status_ids:
            if from_status_id not in assigned_statuses:
                raise HTTPException(
                    status_code=400,
                    detail=("Transition source status is not assigned to this project"),
                )
        if to_status_id not in assigned_statuses:
            raise HTTPException(
                status_code=400,
                detail="Transition target status is not assigned to this project",
            )
        for status_id in {*from_status_ids, to_status_id}:
            status_object_id = parse_object_id(status_id, "status_id")
            if not statuses_collection.find_one({"_id": status_object_id}):
                raise HTTPException(
                    status_code=404,
                    detail=f"Workflow status '{status_id}' not found",
                )

        transition_document = transition.model_dump(mode="json")
        transition_document["project_id"] = project_object_id
        transition_document["from_status_ids"] = from_status_ids
        transition_document["to_status_id"] = to_status_id
        transition_document["guard"]["allowed_roles"] = list(
            dict.fromkeys(transition_document["guard"]["allowed_roles"])
        )
        transition_document["order"] = order
        transition_document["created_at"] = now
        transition_document["created_by"] = str(current_user["_id"])
        transitions.append(transition_document)

    transitions_collection.delete_many({"project_id": project_object_id})
    stored_transitions = []
    for transition in transitions:
        result = transitions_collection.insert_one(transition)
        transition["_id"] = result.inserted_id
        stored_transitions.append(_serialize_transition(transition))

    projects_collection.update_one(
        {"_id": project_object_id},
        {
            "$set": {
                "updated_at": now,
                "updated_by": str(current_user["_id"]),
            },
            "$unset": {"workflow_transitions": ""},
        },
    )
    return {"project_id": project_id, "transitions": stored_transitions}


@router.put("/{project_id}")
def update_project(
    data: ProjectUpdate,
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    project_object_id, project = _get_project_or_404(project_id)
    _require_project_admin(project_object_id, current_user)

    update_data = data.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No project fields supplied")

    if "code" in update_data and update_data["code"] != project.get("code"):
        _ensure_project_key_available(update_data["code"], project_object_id)

    update_data["updated_at"] = datetime.now(timezone.utc)
    update_data["updated_by"] = str(current_user["_id"])
    try:
        projects_collection.update_one(
            {"_id": project_object_id},
            {"$set": update_data},
        )
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Project key already exists")
    return {"message": "Project updated"}


@router.delete("/{project_id}")
@super_admin_required
def delete_project(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    project_object_id, _ = _get_project_or_404(project_id)
    projects_collection.delete_one({"_id": project_object_id})
    project_members_collection.delete_many({"project_id": project_object_id})
    transitions_collection.delete_many({"project_id": project_object_id})
    return {"message": "Project deleted"}


@router.patch("/{project_id}/statuses")
def assign_statuses_to_project(
    project_id: str,
    payload: ProjectStatusAssign,
    current_user: dict = Depends(get_current_user),
):
    project_object_id, _ = _get_project_or_404(project_id)
    _require_project_admin(project_object_id, current_user)

    if len(payload.status_ids) < 2:
        raise HTTPException(
            status_code=400,
            detail="Project must have at least 2 statuses",
        )

    if len(payload.status_ids) != len(set(payload.status_ids)):
        raise HTTPException(
            status_code=400,
            detail="Duplicate status IDs are not allowed",
        )

    statuses = []
    for status_id in payload.status_ids:
        status = statuses_collection.find_one(
            {"_id": parse_object_id(status_id, "status_id")}
        )
        if not status:
            raise HTTPException(
                status_code=404,
                detail=f"Status '{status_id}' not found",
            )
        statuses.append(status)

    statuses.sort(key=lambda status: status["order"])
    ordered_ids = [str(status["_id"]) for status in statuses]

    projects_collection.update_one(
        {"_id": project_object_id},
        {
            "$set": {
                "assigned_statuses": ordered_ids,
                "default_status_id": ordered_ids[0],
                "updated_at": datetime.now(timezone.utc),
                "updated_by": str(current_user["_id"]),
            },
            "$unset": {"workflow_transitions": ""},
        },
    )
    transitions_collection.delete_many({"project_id": project_object_id})

    return {
        "message": "Statuses assigned to project successfully",
        "project_id": project_id,
        "assigned_statuses": [
            {
                "_id": str(status["_id"]),
                "name": status["name"],
                "order": status["order"],
            }
            for status in statuses
        ],
        "first_status": {
            "_id": str(statuses[0]["_id"]),
            "name": statuses[0]["name"],
        },
        "last_status": {
            "_id": str(statuses[-1]["_id"]),
            "name": statuses[-1]["name"],
        },
    }


@router.post("/{project_id}/members")
def add_project_member(
    project_id: str,
    member: ProjectMemberCreate,
    current_user: dict = Depends(get_current_user),
):
    project_object_id, _ = _get_project_or_404(project_id)
    _require_project_admin(project_object_id, current_user)

    member_user_id = parse_object_id(member.user_id, "user_id")
    user = user_collection.find_one({"_id": member_user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    existing_member = project_members_collection.find_one(
        {
            "project_id": project_object_id,
            "user_id": member_user_id,
        }
    )
    if existing_member:
        raise HTTPException(
            status_code=409,
            detail="User is already a member of this project",
        )

    result = project_members_collection.insert_one(
        {
            "project_id": project_object_id,
            "user_id": member_user_id,
            "role": member.role.value,
        }
    )
    return {
        "id": str(result.inserted_id),
        "message": "Project member added successfully",
    }


@router.get("/{project_id}/members")
def get_project_members(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    project_object_id, _ = _get_project_or_404(project_id)
    require_project_membership(
        project_object_id,
        current_user,
        project_members_collection,
        allow_super_admin=True,
    )

    members = []
    for member in project_members_collection.find({"project_id": project_object_id}):
        user = user_collection.find_one(
            {"_id": member["user_id"]},
            {"name": 1, "email": 1},
        )
        if not user:
            continue
        members.append(
            {
                "user_id": str(member["user_id"]),
                "name": user.get("name"),
                "email": user.get("email"),
                "role": member["role"],
            }
        )
    return members
