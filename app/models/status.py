from typing import Optional

from pydantic import BaseModel


class StatusCreate(BaseModel):
    name: str
    order: int


class StatusUpdate(BaseModel):
    name: Optional[str] = None
    order: Optional[int] = None
