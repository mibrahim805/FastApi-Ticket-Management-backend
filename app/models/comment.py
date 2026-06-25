from datetime import datetime

from pydantic import BaseModel

# Unique identifier
# - Linked ticket
# - Author (User)
# - Message body
# - Visibility (Public = visible to customer / Internal = agents only)
# - Created date


class Comment(BaseModel):
    # ticket_id: str
    message: str
    visibility: str  # "public" or "internal"
    created_at: datetime = datetime.now()
