from enum import Enum

from app.models.project_member import ProjectRole


class Permission(str, Enum):
    """Named permissions granted to project roles."""

    # Project management
    VIEW_PROJECT = "view_project"
    MANAGE_PROJECT = "manage_project"
    MANAGE_MEMBERS = "manage_members"
    MANAGE_STATUSES = "manage_statuses"
    MANAGE_WORKFLOW = "manage_workflow"

    # Tickets
    CREATE_TICKET = "create_ticket"
    EDIT_TICKET = "edit_ticket"
    DELETE_TICKET = "delete_ticket"
    ASSIGN_TICKET = "assign_ticket"
    ASSIGN_TICKET_SELF = "assign_ticket_self"
    TRANSITION_TICKET = "transition_ticket"
    LINK_TICKET = "link_ticket"

    # Comments
    COMMENT_PUBLIC = "comment_public"
    COMMENT_INTERNAL = "comment_internal"
    EDIT_OWN_COMMENT = "edit_own_comment"
    DELETE_ANY_COMMENT = "delete_any_comment"

    # Attachments
    UPLOAD_ATTACHMENT = "upload_attachment"
    DELETE_ATTACHMENT = "delete_attachment"

    # Agile
    MANAGE_SPRINTS = "manage_sprints"
    MANAGE_BACKLOG = "manage_backlog"


DEFAULT_PROJECT_ROLE_PERMISSIONS: dict[ProjectRole, frozenset[Permission]] = {
    ProjectRole.LEAD: frozenset(
        {
            Permission.VIEW_PROJECT,
            Permission.MANAGE_PROJECT,
            Permission.MANAGE_MEMBERS,
            Permission.MANAGE_STATUSES,
            Permission.MANAGE_WORKFLOW,
            Permission.CREATE_TICKET,
            Permission.EDIT_TICKET,
            Permission.DELETE_TICKET,
            Permission.ASSIGN_TICKET,
            Permission.TRANSITION_TICKET,
            Permission.LINK_TICKET,
            Permission.COMMENT_PUBLIC,
            Permission.COMMENT_INTERNAL,
            Permission.EDIT_OWN_COMMENT,
            Permission.DELETE_ANY_COMMENT,
            Permission.UPLOAD_ATTACHMENT,
            Permission.DELETE_ATTACHMENT,
            Permission.MANAGE_SPRINTS,
            Permission.MANAGE_BACKLOG,
        }
    ),
    ProjectRole.MEMBER: frozenset(
        {
            Permission.VIEW_PROJECT,
            Permission.CREATE_TICKET,
            Permission.EDIT_TICKET,
            Permission.ASSIGN_TICKET_SELF,
            Permission.TRANSITION_TICKET,
            Permission.LINK_TICKET,
            Permission.COMMENT_PUBLIC,
            Permission.COMMENT_INTERNAL,
            Permission.EDIT_OWN_COMMENT,
            Permission.UPLOAD_ATTACHMENT,
            Permission.MANAGE_BACKLOG,
        }
    ),
    ProjectRole.REPORTER: frozenset(
        {
            Permission.VIEW_PROJECT,
            Permission.CREATE_TICKET,
            Permission.COMMENT_PUBLIC,
            Permission.EDIT_OWN_COMMENT,
            Permission.UPLOAD_ATTACHMENT,
        }
    ),
}
