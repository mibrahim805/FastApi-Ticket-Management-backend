from collections.abc import Collection

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException

from app.models.project_member import ProjectRole

PROJECT_MEMBER_ROLES = frozenset(role.value for role in ProjectRole)
PROJECT_WORK_ROLES = frozenset(
    {
        ProjectRole.LEAD.value,
        ProjectRole.MEMBER.value,
    }
)
PROJECT_LEAD_ROLES = frozenset({ProjectRole.LEAD.value})


def parse_object_id(value: str | ObjectId, field_name: str) -> ObjectId:
    if isinstance(value, ObjectId):
        return value

    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}")


def get_project_membership(
    project_id: str | ObjectId,
    user_id: str | ObjectId,
    project_members_collection,
) -> dict | None:
    return project_members_collection.find_one(
        {
            "project_id": parse_object_id(project_id, "project_id"),
            "user_id": parse_object_id(user_id, "user_id"),
        }
    )


def get_accessible_project_ids(
    current_user: dict,
    project_members_collection,
    projects_collection,
) -> list[str]:
    """Return canonical project IDs the current user is allowed to browse."""
    if current_user.get("role") == "super_admin":
        return [str(project["_id"]) for project in projects_collection.find({})]

    user_id = parse_object_id(current_user.get("_id"), "user_id")
    memberships = project_members_collection.find({"user_id": user_id})
    return list(
        dict.fromkeys(
            str(membership["project_id"])
            for membership in memberships
            if membership.get("project_id")
            and membership.get("role") in PROJECT_MEMBER_ROLES
        )
    )


def require_project_membership(
    project_id: str | ObjectId,
    current_user: dict,
    project_members_collection,
    *,
    allowed_roles: Collection[str] | None = None,
    role_detail: str = "You do not have permission to perform this project action",
    allow_super_admin: bool = False,
) -> dict:
    user_id = current_user.get("_id")
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="Authenticated user is missing an ID",
        )

    if allow_super_admin and current_user.get("role") == "super_admin":
        return {
            "project_id": parse_object_id(project_id, "project_id"),
            "user_id": parse_object_id(user_id, "user_id"),
            "role": "super_admin",
        }

    membership = get_project_membership(
        project_id,
        user_id,
        project_members_collection,
    )
    if not membership:
        raise HTTPException(
            status_code=403,
            detail="You are not a member of this project",
        )

    if membership.get("role") not in PROJECT_MEMBER_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Your project membership has an invalid role",
        )

    if allowed_roles is not None and membership.get("role") not in allowed_roles:
        raise HTTPException(status_code=403, detail=role_detail)

    return membership


def require_ticket_membership(
    ticket: dict,
    current_user: dict,
    project_members_collection,
    *,
    allowed_roles: Collection[str] | None = None,
    role_detail: str = "You do not have permission to perform this ticket action",
) -> dict:
    project_id = ticket.get("project_id")
    if not project_id:
        raise HTTPException(status_code=400, detail="Ticket is missing project_id")

    return require_project_membership(
        project_id,
        current_user,
        project_members_collection,
        allowed_roles=allowed_roles,
        role_detail=role_detail,
    )


def require_ticket_participant(
    ticket: dict,
    current_user: dict,
    project_members_collection,
) -> dict:
    """Require project membership, with support for legacy ticket participants.

    New assignments are restricted to project members. The participant fallbacks
    keep older tickets usable when their creator or assignee predates project
    memberships, and retain the original super-admin override.
    """
    try:
        return require_ticket_membership(
            ticket,
            current_user,
            project_members_collection,
        )
    except HTTPException as error:
        is_missing_membership = (
            error.status_code == 403
            and error.detail == "You are not a member of this project"
        )
        if not is_missing_membership:
            raise

    user_id = current_user.get("_id")
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="Authenticated user is missing an ID",
        )

    user_id = str(user_id)
    if user_id == str(ticket.get("assigned_agent")):
        return {"role": ProjectRole.MEMBER.value, "source": "ticket_assignment"}
    if user_id == str(ticket.get("userId")):
        return {"role": ProjectRole.REPORTER.value, "source": "ticket_creator"}
    if current_user.get("role") == "super_admin":
        return {"role": "super_admin", "source": "global_role"}

    raise HTTPException(
        status_code=403,
        detail="You do not have permission to access this ticket",
    )
