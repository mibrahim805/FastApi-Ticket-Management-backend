from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

NOTIFICATION_EVENT_TYPES = frozenset(
    {
        "ticket_created",
        "ticket_updated",
        "ticket_assigned",
        "ticket_deleted",
        "ticket_transitioned",
        "ticket_linked",
        "comment_created",
        "comment_updated",
        "comment_deleted",
        "comment_mentioned",
    }
)


class Notifications(BaseModel):
    project_id: str | None = None
    comment_id: str | None = None
    ticket_id: str
    user_id: str
    actor_id: str | None = None
    event_type: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    is_read: bool = False
    read_at: datetime | None = None


class NotificationPreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    disabled_event_types: set[str] = Field(default_factory=set)

    @field_validator("disabled_event_types")
    @classmethod
    def validate_event_types(cls, value: set[str]) -> set[str]:
        unknown_event_types = value - NOTIFICATION_EVENT_TYPES
        if unknown_event_types:
            unknown = ", ".join(sorted(unknown_event_types))
            raise ValueError(f"Unsupported notification event types: {unknown}")
        return value
