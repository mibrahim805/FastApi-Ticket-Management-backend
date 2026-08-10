import re
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from app.api.activity import log_project_activity
from app.api.auth import get_current_user
from app.api.permissions import (
    ROLE_CAPABILITIES,
    SUPER_ADMIN_ROLES,
    Capability,
    ensure_role,
    has_capability,
    parse_object_id,
    require_project_membership,
    require_role,
    role_has_permission,
    serialize_capabilities,
)
from app.db.db import (
    project_components_collection,
    project_members_collection,
    projects_collection,
    statuses_collection,
    ticket_collection,
    transitions_collection,
    user_collection,
)
from app.models.permission import Permission
from app.models.project import (
    ProjectCreate,
    ProjectMemberRole,
    ProjectRolePermissionUpdate,
    ProjectStatusAssign,
    ProjectUpdate,
)
from app.models.project_member import ProjectMemberCreate
from app.models.transition import (
    ProjectWorkflowUpdate,
    TransitionCreate,
    TransitionPatch,
)


class ComponentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ComponentUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


def _serialize_component(component: dict) -> dict:
    def convert(value):
        if isinstance(value, ObjectId):
            return str(value)
        if isinstance(value, dict):
            return {key: convert(item) for key, item in value.items()}
        if isinstance(value, list):
            return [convert(item) for item in value]
        if isinstance(value, tuple):
            return [convert(item) for item in value]
        return value

    return convert(component)


router = APIRouter(prefix="/projects", tags=["Projects"])

_require_super_admin = require_role(
    *SUPER_ADMIN_ROLES,
    detail="Only super_admin can perform this action",
)


def _get_project_or_404(project_id: str) -> tuple:
    project_object_id = parse_object_id(project_id, "project_id")
    project = projects_collection.find_one({"_id": project_object_id})
    if not project or project.get("deleted_at") is not None:
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
        capability=Capability.MANAGE_PROJECT,
        role_detail="Only project leads or super admins can configure the project",
        allow_super_admin=True,
    )


@router.post("")
def create_project(
    project: ProjectCreate,
    current_user: dict = Depends(_require_super_admin),
):
    ensure_role(
        current_user,
        SUPER_ADMIN_ROLES,
        detail="Only super_admin can perform this action",
    )
    current_user_id = parse_object_id(current_user.get("_id"), "user_id")
    _ensure_project_key_available(project.code)

    project_document = project.model_dump()
    project_document.update(
        {
            "created_by": str(current_user_id),
            "created_at": datetime.now(timezone.utc),
            "assigned_statuses": [],
            "default_status_id": None,
            "permission_overrides": {},
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
    log_project_activity(
        project_id=str(result.inserted_id),
        action="project_created",
        changed_by=current_user_id,
        changes=[
            {
                "field": "member_role",
                "user_id": str(current_user_id),
                "old_value": None,
                "new_value": ProjectMemberRole.LEAD.value,
            }
        ],
    )

    return {"id": str(result.inserted_id), "message": "Project created"}


@router.get("")
def get_projects(current_user: dict = Depends(get_current_user)):
    user_id = parse_object_id(current_user.get("_id"), "user_id")

    if current_user.get("role") == "super_admin":
        projects = [
            project
            for project in projects_collection.find({})
            if project.get("deleted_at") is None
        ]
    else:
        memberships = list(project_members_collection.find({"user_id": user_id}))
        projects = []
        seen_project_ids = set()
        for membership in memberships:
            if not has_capability(membership, Capability.VIEW_PROJECT):
                continue
            project_id = membership.get("project_id")
            if not project_id or project_id in seen_project_ids:
                continue
            project = projects_collection.find_one({"_id": project_id})
            if project and project.get("deleted_at") is None:
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
        capability=Capability.VIEW_PROJECT,
    )
    return {"project": _serialize_project(project)}


def _permission_override(project: dict, role: str) -> dict:
    override = (project.get("permission_overrides") or {}).get(role) or {}
    return {
        "grants": sorted(set(override.get("grants") or [])),
        "revocations": sorted(set(override.get("revocations") or [])),
    }


def _serialize_project_permissions(project: dict) -> dict:
    roles = {}
    for role in ProjectMemberRole:
        override = _permission_override(project, role.value)
        effective = set(ROLE_CAPABILITIES[role.value])
        effective.difference_update(
            Capability(value)
            for value in override["revocations"]
            if value in Capability._value2member_map_
        )
        effective.update(
            Capability(value)
            for value in override["grants"]
            if value in Capability._value2member_map_
        )
        roles[role.value] = {
            "defaults": sorted(
                capability.value for capability in ROLE_CAPABILITIES[role.value]
            ),
            **override,
            "effective": sorted(capability.value for capability in effective),
        }
    return {"project_id": str(project["_id"]), "roles": roles}


@router.get("/{project_id}/permissions")
def get_project_permissions(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    project_object_id, project = _get_project_or_404(project_id)
    require_project_membership(
        project_object_id,
        current_user,
        project_members_collection,
        allow_super_admin=True,
        capability=Capability.VIEW_PROJECT,
    )
    return _serialize_project_permissions(project)


@router.get("/{project_id}/capabilities")
def get_project_capabilities(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Return the authenticated user's effective capabilities for a project."""
    project_object_id, project = _get_project_or_404(project_id)
    membership = require_project_membership(
        project_object_id,
        current_user,
        project_members_collection,
        allow_super_admin=True,
        capability=Capability.VIEW_PROJECT,
    )
    capabilities = serialize_capabilities(membership)
    return {
        "project_id": str(project_object_id),
        "role": membership.get("role"),
        "capabilities": capabilities,
        "can": {
            capability.value: capability.value in capabilities
            for capability in Capability
        },
    }


@router.get("/{project_id}/my-permissions")
def get_my_project_permissions(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Return the named permissions granted to the authenticated user."""
    project_object_id, _ = _get_project_or_404(project_id)
    membership = require_project_membership(
        project_object_id,
        current_user,
        project_members_collection,
        allow_super_admin=True,
        capability=Capability.VIEW_PROJECT,
    )

    role = membership.get("role")
    permissions = (
        frozenset(Permission)
        if role == "super_admin"
        else frozenset(
            permission
            for permission in Permission
            if role_has_permission(role, permission, project_object_id)
        )
    )
    return {
        "role": role,
        "permissions": sorted(permission.value for permission in permissions),
    }


@router.put("/{project_id}/permissions")
def update_project_permissions(
    project_id: str,
    payload: ProjectRolePermissionUpdate,
    current_user: dict = Depends(get_current_user),
):
    project_object_id, project = _get_project_or_404(project_id)
    _require_project_admin(project_object_id, current_user)

    unknown = (payload.grants | payload.revocations) - {
        capability.value for capability in Capability
    }
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown capabilities: {', '.join(sorted(unknown))}",
        )
    overlap = payload.grants & payload.revocations
    if overlap:
        raise HTTPException(
            status_code=400,
            detail=(
                "Capabilities cannot be both granted and revoked: "
                f"{', '.join(sorted(overlap))}"
            ),
        )
    if Capability.MANAGE_PROJECT.value in payload.grants | payload.revocations:
        raise HTTPException(
            status_code=400,
            detail="manage_project cannot be overridden",
        )

    role = payload.role.value
    previous_override = _permission_override(project, role)
    overrides = dict(project.get("permission_overrides") or {})
    overrides[role] = {
        "grants": sorted(payload.grants),
        "revocations": sorted(payload.revocations),
    }
    projects_collection.update_one(
        {"_id": project_object_id},
        {"$set": {"permission_overrides": overrides}},
    )

    for membership in project_members_collection.find(
        {"project_id": project_object_id, "role": role}
    ):
        project_members_collection.update_one(
            {"_id": membership["_id"]},
            {
                "$set": {
                    "capability_grants": sorted(payload.grants),
                    "capability_revocations": sorted(payload.revocations),
                }
            },
        )

    updated_project = {**project, "permission_overrides": overrides}
    log_project_activity(
        project_id=str(project_object_id),
        action="project_permissions_updated",
        changed_by=current_user["_id"],
        changes=[
            {
                "field": "role_permissions",
                "role": role,
                "old_value": previous_override,
                "new_value": overrides[role],
            }
        ],
    )
    return _serialize_project_permissions(updated_project)


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
        capability=Capability.VIEW_PROJECT,
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


@router.get("/{project_id}/components")
def list_project_components(
    project_id: str, current_user: dict = Depends(get_current_user)
):
    project_object_id, _ = _get_project_or_404(project_id)
    require_project_membership(
        project_object_id,
        current_user,
        project_members_collection,
        allow_super_admin=True,
        capability=Capability.VIEW_PROJECT,
    )
    return [
        _serialize_component(item)
        for item in project_components_collection.find(
            {"project_id": project_object_id}
        ).sort("name", 1)
    ]


@router.post("/{project_id}/components", status_code=201)
def create_project_component(
    project_id: str,
    payload: ComponentCreate,
    current_user: dict = Depends(get_current_user),
):
    project_object_id, _ = _get_project_or_404(project_id)
    _require_project_admin(project_object_id, current_user)
    name = payload.name.strip()
    if project_components_collection.find_one(
        {"project_id": project_object_id, "name_key": name.lower()}
    ):
        raise HTTPException(status_code=409, detail="Component already exists")
    item = {
        "project_id": project_object_id,
        "name": name,
        "name_key": name.lower(),
        "created_at": datetime.now(timezone.utc),
    }
    result = project_components_collection.insert_one(item)
    item["_id"] = result.inserted_id
    return _serialize_component(item)


@router.patch("/{project_id}/components/{component_id}")
def update_project_component(
    project_id: str,
    component_id: str,
    payload: ComponentUpdate,
    current_user: dict = Depends(get_current_user),
):
    project_object_id, _ = _get_project_or_404(project_id)
    _require_project_admin(project_object_id, current_user)
    component_object_id = parse_object_id(component_id, "component_id")
    name = payload.name.strip()
    item = project_components_collection.find_one(
        {"_id": component_object_id, "project_id": project_object_id}
    )
    if not item:
        raise HTTPException(status_code=404, detail="Component not found")
    duplicate = project_components_collection.find_one(
        {
            "project_id": project_object_id,
            "name_key": name.lower(),
            "_id": {"$ne": component_object_id},
        }
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="Component already exists")
    project_components_collection.update_one(
        {"_id": component_object_id}, {"$set": {"name": name, "name_key": name.lower()}}
    )
    item.update({"name": name, "name_key": name.lower()})
    return _serialize_component(item)


@router.delete("/{project_id}/components/{component_id}", status_code=204)
def delete_project_component(
    project_id: str, component_id: str, current_user: dict = Depends(get_current_user)
):
    project_object_id, _ = _get_project_or_404(project_id)
    _require_project_admin(project_object_id, current_user)
    component_object_id = parse_object_id(component_id, "component_id")
    if ticket_collection.find_one({"component_ids": component_object_id}):
        raise HTTPException(
            status_code=409,
            detail="Cannot delete a component referenced by tickets",
        )
    result = project_components_collection.delete_one(
        {
            "_id": component_object_id,
            "project_id": project_object_id,
        }
    )
    if not result.deleted_count:
        raise HTTPException(status_code=404, detail="Component not found")


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


def _validate_transition(project: dict, payload: TransitionCreate) -> dict:
    assigned = {str(value) for value in project.get("assigned_statuses", [])}
    sources = [
        str(parse_object_id(value, "status_id")) for value in payload.from_status_ids
    ]
    target = str(parse_object_id(payload.to_status_id, "status_id"))
    if len(sources) != len(set(sources)):
        raise HTTPException(
            status_code=400, detail="Transition source status IDs must be unique"
        )
    for status_id in [*sources, target]:
        if status_id not in assigned:
            raise HTTPException(
                status_code=400,
                detail="Transition status is not assigned to this project",
            )
        if not statuses_collection.find_one(
            {"_id": parse_object_id(status_id, "status_id")}
        ):
            raise HTTPException(
                status_code=404, detail=f"Workflow status '{status_id}' not found"
            )
    document = payload.model_dump(mode="json")
    document["project_id"] = project["_id"]
    document["from_status_ids"] = sources
    document["to_status_id"] = target
    document["guard"]["allowed_roles"] = list(
        dict.fromkeys(document["guard"]["allowed_roles"])
    )
    return document


@router.post("/{project_id}/workflow/transitions", status_code=201)
def add_workflow_transition(
    project_id: str,
    payload: TransitionCreate,
    current_user: dict = Depends(get_current_user),
):
    project_object_id, project = _get_project_or_404(project_id)
    _require_project_admin(project_object_id, current_user)
    document = _validate_transition(project, payload)
    document.update(
        {
            "order": transitions_collection.count_documents(
                {"project_id": project_object_id}
            ),
            "created_at": datetime.now(timezone.utc),
            "created_by": str(current_user["_id"]),
        }
    )
    document["name_key"] = document["name"].strip().casefold()
    try:
        result = transitions_collection.insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Transition already exists")
    document["_id"] = result.inserted_id
    return _serialize_transition(document)


@router.patch("/{project_id}/workflow/transitions/{transition_id}")
def edit_workflow_transition(
    project_id: str,
    transition_id: str,
    payload: TransitionPatch,
    current_user: dict = Depends(get_current_user),
):
    project_object_id, project = _get_project_or_404(project_id)
    _require_project_admin(project_object_id, current_user)
    transition_object_id = parse_object_id(transition_id, "transition_id")
    existing = transitions_collection.find_one(
        {"_id": transition_object_id, "project_id": project_object_id}
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Workflow transition not found")
    merged = {**existing, **payload.model_dump(exclude_none=True)}
    document = _validate_transition(project, TransitionCreate.model_validate(merged))
    transitions_collection.update_one({"_id": transition_object_id}, {"$set": document})
    document["_id"] = transition_object_id
    return _serialize_transition(document)


@router.delete("/{project_id}/workflow/transitions/{transition_id}", status_code=204)
def delete_workflow_transition(
    project_id: str, transition_id: str, current_user: dict = Depends(get_current_user)
):
    project_object_id, _ = _get_project_or_404(project_id)
    _require_project_admin(project_object_id, current_user)
    result = transitions_collection.delete_one(
        {
            "_id": parse_object_id(transition_id, "transition_id"),
            "project_id": project_object_id,
        }
    )
    if not result.deleted_count:
        raise HTTPException(status_code=404, detail="Workflow transition not found")


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
def delete_project(
    project_id: str,
    current_user: dict = Depends(_require_super_admin),
):
    ensure_role(
        current_user,
        SUPER_ADMIN_ROLES,
        detail="Only super_admin can perform this action",
    )
    project_object_id, _ = _get_project_or_404(project_id)
    projects_collection.update_one(
        {"_id": project_object_id},
        {
            "$set": {
                "deleted_at": datetime.now(timezone.utc),
                "deleted_by": str(current_user["_id"]),
                "deletion_reason": "deleted_by_super_admin",
                "status": "archived",
            }
        },
    )
    return {"message": "Project archived successfully"}


@router.patch("/{project_id}/statuses")
def assign_statuses_to_project(
    project_id: str,
    payload: ProjectStatusAssign,
    current_user: dict = Depends(get_current_user),
):
    project_object_id, project = _get_project_or_404(project_id)
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

    existing_status_ids = {
        str(status_id) for status_id in project.get("assigned_statuses", [])
    }
    removed_status_ids = existing_status_ids - set(payload.status_ids)
    migration_ids = [
        migration.remove_status_id for migration in payload.status_migrations
    ]
    if len(migration_ids) != len(set(migration_ids)):
        raise HTTPException(
            status_code=400,
            detail="Duplicate status migrations are not allowed",
        )
    migrations = {
        migration.remove_status_id: migration.move_existing_tickets_to
        for migration in payload.status_migrations
    }
    missing_migrations = sorted(removed_status_ids - migrations.keys())
    if missing_migrations:
        raise HTTPException(
            status_code=400,
            detail=(
                "Each removed status requires a migration target: "
                + ", ".join(missing_migrations)
            ),
        )
    unknown_migrations = set(migrations) - removed_status_ids
    if unknown_migrations:
        raise HTTPException(
            status_code=400,
            detail="Status migrations may only reference statuses being removed",
        )
    invalid_targets = set(migrations.values()) - set(payload.status_ids)
    if invalid_targets:
        raise HTTPException(
            status_code=400,
            detail="Status migration targets must be assigned to the project",
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

    status_names = {str(status["_id"]): status["name"] for status in statuses}
    for removed_status_id, target_status_id in migrations.items():
        ticket_collection.update_many(
            {
                "project_id": {"$in": [str(project_object_id), project_object_id]},
                "current_status_id": removed_status_id,
            },
            {
                "$set": {
                    "current_status_id": target_status_id,
                    "current_status_name": status_names[target_status_id],
                }
            },
        )

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
    project_object_id, project = _get_project_or_404(project_id)
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

    override = _permission_override(project, member.role.value)
    try:
        result = project_members_collection.insert_one(
            {
                "project_id": project_object_id,
                "user_id": member_user_id,
                "role": member.role.value,
                "capability_grants": override["grants"],
                "capability_revocations": override["revocations"],
            }
        )
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409, detail="User is already a member of this project"
        )
    log_project_activity(
        project_id=str(project_object_id),
        action="project_member_added",
        changed_by=current_user["_id"],
        changes=[
            {
                "field": "member_role",
                "user_id": str(member_user_id),
                "old_value": None,
                "new_value": member.role.value,
            },
            {
                "field": "member_capability_overrides",
                "user_id": str(member_user_id),
                "old_value": None,
                "new_value": override,
            },
        ],
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
        capability=Capability.VIEW_PROJECT,
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
