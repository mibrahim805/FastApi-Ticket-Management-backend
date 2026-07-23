from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class CommentVisibility(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"


class Comment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)
    visibility: CommentVisibility


class CommentReply(BaseModel):
    message: str = Field(min_length=1)


class CommentUpdate(BaseModel):
    message: str | None = Field(default=None, min_length=1)
    visibility: CommentVisibility | None = None
