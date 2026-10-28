from typing import List, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.project_member import ProjectRole

PROJECT_KEY_PATTERN = r"^[A-Z][A-Z0-9]{1,9}$"
PROJECT_KEY_DESCRIPTION = (
    "Unique project key: 2-10 characters, beginning with an uppercase "
    "letter and containing only uppercase letters or digits"
)


def _validate_repository_url(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    value = value.strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Repository URL must be a valid HTTP or HTTPS URL")
    return value


class Project(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    code: str = Field(
        min_length=2,
        max_length=10,
        pattern=PROJECT_KEY_PATTERN,
        description=PROJECT_KEY_DESCRIPTION,
    )
    repository_url: Optional[str] = Field(default=None, max_length=2048)
    created_by: str
    total_tickets: int = 0
    assigned_statuses: List[str] = Field(default_factory=list)
    default_status_id: Optional[str] = None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    code: str = Field(
        min_length=2,
        max_length=10,
        pattern=PROJECT_KEY_PATTERN,
        description=PROJECT_KEY_DESCRIPTION,
    )
    repository_url: Optional[str] = Field(default=None, max_length=2048)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Project name cannot be blank")
        return value

    @field_validator("repository_url")
    @classmethod
    def validate_repository_url(cls, value: Optional[str]) -> Optional[str]:
        return _validate_repository_url(value)


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    code: Optional[str] = Field(
        default=None,
        min_length=2,
        max_length=10,
        pattern=PROJECT_KEY_PATTERN,
        description=PROJECT_KEY_DESCRIPTION,
    )
    repository_url: Optional[str] = Field(default=None, max_length=2048)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("Project name cannot be blank")
        return value

    @field_validator("repository_url")
    @classmethod
    def validate_repository_url(cls, value: Optional[str]) -> Optional[str]:
        return _validate_repository_url(value)


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


class StatusMigration(BaseModel):
    remove_status_id: str
    move_existing_tickets_to: str


class ProjectStatusAssign(BaseModel):
    status_ids: List[str]
    status_migrations: List[StatusMigration] = Field(default_factory=list)


class ProjectRolePermissionUpdate(BaseModel):
    role: ProjectRole
    grants: set[str] = Field(default_factory=set)
    revocations: set[str] = Field(default_factory=set)
