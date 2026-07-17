from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.project_member import ProjectRole

PROJECT_KEY_PATTERN = r"^[A-Z][A-Z0-9]{1,9}$"
PROJECT_KEY_DESCRIPTION = (
    "Unique project key: 2-10 characters, beginning with an uppercase "
    "letter and containing only uppercase letters or digits"
)


class WorkflowGuard(BaseModel):
    allowed_roles: List[ProjectRole] = Field(
        default_factory=lambda: [ProjectRole.LEAD, ProjectRole.MEMBER],
        min_length=1,
    )
    requires_assignee: bool = False


class WorkflowTransition(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    from_status_id: str
    to_status_id: str
    guards: WorkflowGuard = Field(default_factory=WorkflowGuard)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Transition name cannot be blank")
        return value


class ProjectWorkflowUpdate(BaseModel):
    transitions: List[WorkflowTransition] = Field(default_factory=list)


class Project(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    code: str = Field(
        min_length=2,
        max_length=10,
        pattern=PROJECT_KEY_PATTERN,
        description=PROJECT_KEY_DESCRIPTION,
    )
    created_by: str
    total_tickets: int = 0
    assigned_statuses: List[str] = Field(default_factory=list)
    default_status_id: Optional[str] = None
    workflow_transitions: List[WorkflowTransition] = Field(default_factory=list)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    code: str = Field(
        min_length=2,
        max_length=10,
        pattern=PROJECT_KEY_PATTERN,
        description=PROJECT_KEY_DESCRIPTION,
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Project name cannot be blank")
        return value


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    code: Optional[str] = Field(
        default=None,
        min_length=2,
        max_length=10,
        pattern=PROJECT_KEY_PATTERN,
        description=PROJECT_KEY_DESCRIPTION,
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("Project name cannot be blank")
        return value


class ProjectMember(BaseModel):
    project_id: str
    user_id: str
    role: ProjectRole


ProjectMemberRole = ProjectRole


class ProjectMemberResponse(BaseModel):
    id: str
    project_id: str
    user_id: str
    role: ProjectMemberRole


class ProjectStatusAssign(BaseModel):
    status_ids: List[str]
