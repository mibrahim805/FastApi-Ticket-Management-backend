from app.api.permissions import role_has_permission
from app.models.permission import DEFAULT_PROJECT_ROLE_PERMISSIONS, Permission
from app.models.project_member import ProjectRole

EXPECTED_PERMISSIONS = {
    ProjectRole.LEAD: {
        "view_project",
        "manage_project",
        "manage_members",
        "manage_statuses",
        "manage_workflow",
        "create_ticket",
        "edit_ticket",
        "delete_ticket",
        "assign_ticket",
        "transition_ticket",
        "link_ticket",
        "comment_public",
        "comment_internal",
        "edit_own_comment",
        "delete_any_comment",
        "upload_attachment",
        "delete_attachment",
        "manage_sprints",
        "manage_backlog",
    },
    ProjectRole.MEMBER: {
        "view_project",
        "create_ticket",
        "edit_ticket",
        "assign_ticket_self",
        "transition_ticket",
        "link_ticket",
        "comment_public",
        "comment_internal",
        "edit_own_comment",
        "upload_attachment",
        "manage_backlog",
    },
    ProjectRole.REPORTER: {
        "view_project",
        "create_ticket",
        "comment_public",
        "edit_own_comment",
        "upload_attachment",
    },
}


def test_default_project_role_permission_matrix_is_golden():
    actual = {
        role: {permission.value for permission in permissions}
        for role, permissions in DEFAULT_PROJECT_ROLE_PERMISSIONS.items()
    }
    assert actual == EXPECTED_PERMISSIONS


def test_role_has_permission_uses_default_matrix():
    for role, permissions in EXPECTED_PERMISSIONS.items():
        for permission in Permission:
            assert role_has_permission(role.value, permission) is (
                permission.value in permissions
            )
