from typing import Optional

from pydantic import BaseModel, Field, field_validator


class StatusCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    order: int = Field(ge=0)
    key: Optional[str] = None
    category: Optional[str] = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class StatusUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=50)
    order: Optional[int] = Field(default=None, ge=0)
    key: Optional[str] = None
    category: Optional[str] = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value
