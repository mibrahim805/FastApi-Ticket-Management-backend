from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.project_member import ProjectRole


class Project(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    code: str = Field(
        min_length=2,
        max_length=10,
        description=(
            "Unique project key: 2-10 characters, beginning with an uppercase "
            "letter and containing only uppercase letters or digits"
        ),
    )
    created_by: str
    total_tickets: int = 0
    assigned_statuses: List[str] = Field(default_factory=list)
    default_status_id: Optional[str] = None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    code: str = Field(
        min_length=2,
        max_length=10,
        description=(
            "Unique project key: 2-10 characters, beginning with an uppercase "
            "letter and containing only uppercase letters or digits"
        ),
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
        description=(
            "Unique project key: 2-10 characters, beginning with an uppercase "
            "letter and containing only uppercase letters or digits"
        ),
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
