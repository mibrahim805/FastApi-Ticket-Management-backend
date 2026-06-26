import enum
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class Priority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Status(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class Category(str, enum.Enum):
    BUG = "bug"
    FEATURE_REQUEST = "feature_request"
    SUPPORT = "support"


class Ticket(BaseModel):
    board_id: str
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
    board_id: str
    title: str
    description: str
    category: Category
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
