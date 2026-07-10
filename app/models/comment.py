from datetime import datetime

from pydantic import BaseModel


class Comment(BaseModel):
    # ticket_id: str
    message: str
    visibility: str  # "public" or "internal"
    created_at: datetime = datetime.now()


# hj vjhdvhd vjds csdj c
