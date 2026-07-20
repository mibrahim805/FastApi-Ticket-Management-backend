import enum
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class IssueLinkType(str, enum.Enum):
    BLOCKS = "blocks"
    RELATES_TO = "relates_to"
    DUPLICATES = "duplicates"


class IssueLinkCreate(BaseModel):
    to_ticket_id: str = Field(description="ID of the issue to link")
    link_type: IssueLinkType


class Ticket(BaseModel):
    project_id: str
    project_key: str
    ticket_id: str
    ticket_code: str
    title: str
    description: str
    category: Category
    issue_type: IssueType = IssueType.TASK
    epic_id: Optional[str] = None
    labels: List[str] = Field(default_factory=list)
    watchers: List[str] = Field(default_factory=list)
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
    epic_id: Optional[str] = Field(
        default=None,
        description="ID of the epic that contains this issue",
    )
    labels: List[str] = Field(default_factory=list)
    priority: Priority
    # created_at: datetime = datetime.now()
    # due_date: datetime


class TicketUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    category: Optional[str] = None
    labels: Optional[List[str]] = None
    due_date: Optional[datetime] = None


class AssignAgent(BaseModel):
    agent_id: str


class BulkTransitionRequest(BaseModel):
    ticket_ids: List[str] = Field(min_length=1, max_length=100)
    transition_id: str

    @field_validator("ticket_ids")
    @classmethod
    def validate_unique_ticket_ids(cls, value: List[str]) -> List[str]:
        if len(value) != len(set(value)):
            raise ValueError("ticket_ids must be unique")
        return value


class BulkAssignRequest(BaseModel):
    ticket_ids: List[str] = Field(min_length=1, max_length=100)
    agent_id: str

    @field_validator("ticket_ids")
    @classmethod
    def validate_unique_ticket_ids(cls, value: List[str]) -> List[str]:
        if len(value) != len(set(value)):
            raise ValueError("ticket_ids must be unique")
        return value
