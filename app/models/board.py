import enum
from typing import List, Optional

from pydantic import BaseModel

from app.models.project_member import ProjectRole


class Board(BaseModel):
    title: str
    code: str
    ticket_counter: int = 0
    created_by: str
    total_tickets: int
    # assigned_agent: str
    assigned_statuses: List[str] = []
    default_status_id: Optional[str] = None


class Config:
    orm_mode = True


class BoardCreate(BaseModel):
    name: str
    code: str


class BoardUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    # assigned_agent: Optional[str] = None
    assigned_statuses: Optional[List[str]] = None
    default_status_id: Optional[str] = None


class BoardMember(BaseModel):
    board_id: str
    user_id: str
    role: ProjectRole


class BoardRole(str, enum.Enum):
    LEAD = "lead"
    MEMBER = "member"
    REPORTER = "reporter"


class BoardMemberCreate(BaseModel):
    user_id: str
    role: BoardRole = BoardRole.MEMBER


class BoardMemberResponse(BaseModel):
    id: str
    board_id: str
    user_id: str
    role: BoardRole


class AssignBoard(BaseModel):
    agent_id: str


class BoardStatusAssign(BaseModel):
    status_ids: List[str]
