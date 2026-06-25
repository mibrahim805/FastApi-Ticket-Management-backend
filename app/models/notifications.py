from datetime import datetime

from pydantic import BaseModel





class Notifications(BaseModel):
    comment_id: str = None
    ticket_id: str
    user_id: str
    event_type: str
    created_at: datetime = datetime.now()



