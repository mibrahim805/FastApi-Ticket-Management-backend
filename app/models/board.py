from typing import List, Optional

from pydantic import BaseModel


class Board(BaseModel):
    title: str
    code: str
    ticket_counter: int = 0
    created_by: str
    total_tickets: int
    assigned_agent: str
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
    assigned_agent: Optional[str] = None
    assigned_statuses: Optional[List[str]] = None
    default_status_id: Optional[str] = None


class AssignBoard(BaseModel):
    agent_id: str


class BoardStatusAssign(BaseModel):
    status_ids: List[str]
