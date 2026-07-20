from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SavedFilterCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    query_params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Filter name cannot be empty")
        return normalized
