from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from app.models.project_member import ProjectRole


class TransitionGuard(BaseModel):
    allowed_roles: List[ProjectRole] = Field(
        default_factory=lambda: [ProjectRole.LEAD, ProjectRole.MEMBER],
        min_length=1,
    )
    requires_assignee: bool = False


class TransitionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    from_status_ids: List[str] = Field(min_length=1)
    to_status_id: str
    guard: TransitionGuard = Field(default_factory=TransitionGuard)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Transition name cannot be blank")
        return value

    @field_validator("from_status_ids")
    @classmethod
    def validate_unique_sources(cls, value: List[str]) -> List[str]:
        if len(value) != len(set(value)):
            raise ValueError("from_status_ids must be unique")
        return value


class ProjectWorkflowUpdate(BaseModel):
    transitions: List[TransitionCreate] = Field(default_factory=list)


class TransitionPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    from_status_ids: Optional[List[str]] = None
    to_status_id: Optional[str] = None
    guard: Optional[TransitionGuard] = None
