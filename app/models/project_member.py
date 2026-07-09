from enum import Enum

from pydantic import BaseModel


class ProjectRole(str, Enum):
    LEAD = "lead"
    MEMBER = "member"
    REPORTER = "reporter"


class ProjectMemberCreate(BaseModel):
    project_id: str
    user_id: str
    role: ProjectRole = ProjectRole.MEMBER
