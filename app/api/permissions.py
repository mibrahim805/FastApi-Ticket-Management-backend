from collections.abc import Collection
from enum import Enum

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import Depends, HTTPException

from app.api.auth import get_current_user
from app.models.project_member import ProjectRole
from app.models.registration import GlobalRole

SUPER_ADMIN_ROLES = frozenset({GlobalRole.SUPER_ADMIN.value})
ADMIN_ROLES = frozenset({GlobalRole.ADMIN.value, GlobalRole.SUPER_ADMIN.value})
PROJECT_MEMBER_ROLES = frozenset(role.value for role in ProjectRole)


class Capability(str, Enum):
    """Actions governed by a project's membership role.

    Keep authorization decisions expressed in terms of capabilities at call
    sites.  The role-to-capability mapping is deliberately kept here so a new
    endpoint cannot accidentally invent a different interpretation of a role.
    """

    VIEW_PROJECT = "view_project"
    VIEW_TICKET = "view_ticket"
    CREATE_TICKET = "create_ticket"
    UPDATE_TICKET = "update_ticket"
    MANAGE_TICKET_WORK = "manage_ticket_work"
    TRANSITION_TICKETS = "transition_tickets"
    ASSIGN_TICKETS = "assign_tickets"
    REASSIGN_TICKETS = "reassign_tickets"
    LINK_TICKETS = "link_tickets"
    MANAGE_SPRINTS = "manage_sprints"
    MANAGE_PROJECT = "manage_project"
    VIEW_REPORTS = "view_reports"
    DELETE_TICKETS = "delete_tickets"
    VIEW_INTERNAL_COMMENTS = "view_internal_comments"
    CREATE_INTERNAL_COMMENTS = "create_internal_comments"
    CREATE_PUBLIC_COMMENTS = "create_public_comments"
    MANAGE_NOTIFICATION_PREFERENCES = "manage_notification_preferences"
    VIEW_ATTACHMENTS = "view_attachments"
    UPLOAD_ATTACHMENTS = "upload_attachments"
    DELETE_ATTACHMENTS = "delete_attachments"


# Public policy table: this is the source of truth for role-based access.
# Contextual permissions (for example, ticket creator/assignee) are listed
# separately below and must not be inferred from a project role.
ROLE_CAPABILITIES = {
    ProjectRole.LEAD.value: frozenset(Capability),
    ProjectRole.MEMBER.value: frozenset(
        {
            Capability.VIEW_PROJECT,
            Capability.VIEW_TICKET,
            Capability.CREATE_TICKET,
            Capability.UPDATE_TICKET,
            Capability.MANAGE_TICKET_WORK,
            Capability.TRANSITION_TICKETS,
            Capability.ASSIGN_TICKETS,
            Capability.LINK_TICKETS,
            Capability.VIEW_INTERNAL_COMMENTS,
            Capability.CREATE_INTERNAL_COMMENTS,
            Capability.CREATE_PUBLIC_COMMENTS,
            Capability.MANAGE_NOTIFICATION_PREFERENCES,
            Capability.VIEW_ATTACHMENTS,
            Capability.VIEW_REPORTS,
        }
    ),
    ProjectRole.REPORTER.value: frozenset(
        {
            Capability.VIEW_PROJECT,
            Capability.VIEW_TICKET,
            Capability.CREATE_TICKET,
            Capability.CREATE_PUBLIC_COMMENTS,
            Capability.MANAGE_NOTIFICATION_PREFERENCES,
            Capability.VIEW_ATTACHMENTS,
            Capability.VIEW_REPORTS,
        }
    ),
}

# Attachment permissions intentionally have two layers:
#
#   role matrix: lead -> upload/delete, member/reporter -> neither
#   ticket actor exception: creator or current assignee -> upload
#
# This makes the answer to "can a reporter upload?" explicit: not by role;
# only when the reporter is the ticket creator or current assignee.
ATTACHMENT_ACTOR_EXCEPTIONS = frozenset({"ticket_creator", "ticket_assignee"})


def effective_capabilities(membership: dict | None) -> frozenset[Capability]:
    """Resolve global role defaults plus this project's grants/revocations."""
    if not membership:
        return frozenset()
    role = membership.get("role")
    if role == "super_admin":
        return frozenset(Capability)

    capabilities = set(ROLE_CAPABILITIES.get(role, frozenset()))
    for value in membership.get("capability_revocations") or []:
        try:
            capabilities.discard(Capability(value))
        except ValueError:
            continue
    for value in membership.get("capability_grants") or []:
        try:
            capabilities.add(Capability(value))
        except ValueError:
            continue
    return frozenset(capabilities)


def has_capability(membership: dict | None, capability: Capability | str) -> bool:
    """Return whether effective project permissions grant ``capability``."""
    try:
        capability = Capability(capability)
    except ValueError:
        return False
    return capability in effective_capabilities(membership)


def can_upload_attachment(
    ticket: dict,
    current_user: dict,
    membership: dict | None,
) -> bool:
    """Evaluate the complete attachment-upload policy in one place."""
    user_id = str(current_user.get("_id"))
    return any(
        (
            has_capability(membership, Capability.UPLOAD_ATTACHMENTS),
            user_id == str(ticket.get("userId")),
            user_id == str(ticket.get("assigned_agent")),
            current_user.get("role") == "super_admin",
        )
    )


def can_delete_attachment(
    attachment: dict,
    current_user: dict,
    membership: dict | None,
) -> bool:
    """Evaluate the complete attachment-delete policy in one place."""
    return any(
        (
            has_capability(membership, Capability.DELETE_ATTACHMENTS),
            str(attachment.get("uploaded_by")) == str(current_user.get("_id")),
            current_user.get("role") == "super_admin",
        )
    )


def can_assign_ticket(
    ticket: dict,
    current_user: dict,
    membership: dict | None,
    target_user_id: str | ObjectId,
) -> bool:
    """Evaluate lead reassignment and member self-assignment in one place."""
    if has_capability(membership, Capability.REASSIGN_TICKETS):
        return True
    if not has_capability(membership, Capability.ASSIGN_TICKETS):
        return False

    current_user_id = str(current_user.get("_id"))
    return current_user_id == str(target_user_id) and ticket.get("assigned_agent") in (
        None,
        current_user_id,
    )


def transition_guard_error(
    transition: dict,
    ticket: dict,
    membership: dict | None,
) -> tuple[int, str] | None:
    """Evaluate data-driven workflow restrictions outside endpoint code."""
    guard = transition.get("guard", {})
    allowed_roles = {
        role.value if isinstance(role, ProjectRole) else role
        for role in guard.get(
            "allowed_roles",
            [ProjectRole.LEAD.value, ProjectRole.MEMBER.value],
        )
    }
    if not membership or membership.get("role") not in allowed_roles:
        return 403, "Your project role cannot use this transition"
    if guard.get("requires_assignee") and not ticket.get("assigned_agent"):
        return 400, "Ticket must be assigned before using this transition"
    return None


def require_capability(
    membership: dict | None,
    capability: Capability | str,
    *,
    detail: str = "You do not have permission to perform this action",
) -> dict:
    if not has_capability(membership, capability):
        raise HTTPException(status_code=403, detail=detail)
    return membership


def require_project_capability(
    project_id: str | ObjectId,
    current_user: dict,
    project_members_collection,
    capability: Capability | str,
    *,
    detail: str = "You do not have permission to perform this project action",
    allow_super_admin: bool = False,
) -> dict:
    membership = require_project_membership(
        project_id,
        current_user,
        project_members_collection,
        allow_super_admin=allow_super_admin,
    )
    return require_capability(membership, capability, detail=detail)


def require_ticket_capability(
    ticket: dict,
    current_user: dict,
    project_members_collection,
    capability: Capability | str,
    *,
    detail: str = "You do not have permission to perform this ticket action",
) -> dict:
    membership = require_ticket_membership(
        ticket,
        current_user,
        project_members_collection,
    )
    return require_capability(membership, capability, detail=detail)


def ensure_role(
    current_user: dict | None,
    allowed_roles: Collection[str],
    *,
    detail: str = "You do not have permission to perform this action",
) -> dict:
    """Validate a global application role for direct/internal function calls."""
    if not current_user:
        raise HTTPException(status_code=401, detail="User not authenticated")
    if current_user.get("role") not in allowed_roles:
        raise HTTPException(status_code=403, detail=detail)
    return current_user


def require_role(
    *allowed_roles: str,
    detail: str = "You do not have permission to perform this action",
):
    """Build a FastAPI dependency that permits only the supplied global roles."""
    roles = frozenset(allowed_roles)
    if not roles:
        raise ValueError("At least one role is required")

    def dependency(current_user: dict = Depends(get_current_user)) -> dict:
        return ensure_role(current_user, roles, detail=detail)

    return dependency


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
            and has_capability(membership, Capability.VIEW_PROJECT)
        )
    )


def require_project_membership(
    project_id: str | ObjectId,
    current_user: dict,
    project_members_collection,
    *,
    capability: Capability | str | None = None,
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

    if capability is not None:
        require_capability(membership, capability, detail=role_detail)

    return membership


def require_ticket_membership(
    ticket: dict,
    current_user: dict,
    project_members_collection,
    *,
    capability: Capability | str | None = None,
    role_detail: str = "You do not have permission to perform this ticket action",
) -> dict:
    project_id = ticket.get("project_id")
    if not project_id:
        raise HTTPException(status_code=400, detail="Ticket is missing project_id")

    return require_project_membership(
        project_id,
        current_user,
        project_members_collection,
        capability=capability,
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
