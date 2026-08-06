from typing import Optional

from pydantic import BaseModel


class StatusCreate(BaseModel):
    name: str
    order: int
    key: Optional[str] = None
    category: Optional[str] = None


class StatusUpdate(BaseModel):
    name: Optional[str] = None
    order: Optional[int] = None
    key: Optional[str] = None
    category: Optional[str] = None
