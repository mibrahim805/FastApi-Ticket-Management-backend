from datetime import datetime
from typing import Any

from pydantic import BaseModel


class TicketActivity(BaseModel):
    ticket_id: str
    action: str
    field: str | None = None
    old_value: Any = None
    new_value: Any = None
    changed_by: str
    changed_at: datetime
