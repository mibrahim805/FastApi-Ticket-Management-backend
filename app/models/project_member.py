from enum import Enum

from pydantic import BaseModel


class ProjectRole(str, Enum):
    LEAD = "lead"
    MEMBER = "member"
    REPORTER = "reporter"


class ProjectMemberCreate(BaseModel):
    user_id: str
    role: ProjectRole = ProjectRole.MEMBER


class ProjectMemberRoleUpdate(BaseModel):
    role: ProjectRole


class ProjectMemberSuspension(BaseModel):
    suspended: bool
