from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _comparable_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class SprintStatus(str, Enum):
    PLANNED = "planned"
    ACTIVE = "active"
    COMPLETED = "completed"


class SprintCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    goal: Optional[str] = Field(default=None, max_length=1000)
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Sprint name cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_dates(self):
        if (
            self.start_date is not None
            and self.end_date is not None
            and _comparable_datetime(self.end_date)
            <= _comparable_datetime(self.start_date)
        ):
            raise ValueError("end_date must be later than start_date")
        return self


class SprintUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    goal: Optional[str] = Field(default=None, max_length=1000)
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("Sprint name cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_dates(self):
        if (
            self.start_date is not None
            and self.end_date is not None
            and _comparable_datetime(self.end_date)
            <= _comparable_datetime(self.start_date)
        ):
            raise ValueError("end_date must be later than start_date")
        return self


class TicketSprintUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Required but nullable: null explicitly moves the ticket to the backlog.
    sprint_id: Optional[str]


class StoryPointsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Null clears an estimate. A generous upper bound prevents accidental input.
    story_points: Optional[int] = Field(ge=0, le=1000)


class TicketPositionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: int = Field(ge=0)
