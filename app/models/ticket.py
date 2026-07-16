import enum
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Priority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class Status(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    ON_HOLD = "on_hold"
    RESOLVED = "resolved"
    REOPENED = "reopened"
    CLOSED = "closed"


class Category(str, enum.Enum):
    BUG = "bug"
    FEATURE_REQUEST = "feature_request"
    SUPPORT = "support"


class IssueType(str, enum.Enum):
    BUG = "bug"
    TASK = "task"
    STORY = "story"
    EPIC = "epic"
    SUBTASK = "subtask"


class Ticket(BaseModel):
    project_id: str
    project_key: str
    ticket_id: str
    ticket_code: str
    title: str
    description: str
    category: Category
    priority: Priority
    status: Status = Status.OPEN
    created_by: str
    assigned_to: str
    created_at: datetime
    last_updated: datetime
    resolved_date: Optional[datetime] = None
    closed_date: Optional[datetime] = None
    due_date: Optional[datetime] = None


class TicketCreate(BaseModel):
    project_id: str = Field(
        description="Project ID that will own the ticket",
    )
    title: str
    description: str
    category: Category
    issue_type: IssueType = IssueType.TASK
    priority: Priority
    # created_at: datetime = datetime.now()
    # due_date: datetime


class TicketUpdate(BaseModel):
    status: str
    priority: str
    category: str
    assigned_to: str


class AssignAgent(BaseModel):
    agent_id: str


class TicketStatusMove(BaseModel):
    # direction: Optional[Literal["forward", "backward"]] = None
    target_status_id: Optional[str] = None
    target_status_name: Optional[str] = None
    # comment: Optional[str] = None
