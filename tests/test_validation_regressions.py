import pytest
from pydantic import ValidationError

from app.models.status import StatusCreate, StatusUpdate
from app.models.ticket import Category, Priority, TicketUpdate


def test_status_create_normalizes_and_bounds_values():
    assert StatusCreate(name="  Ready  ", order=0).name == "Ready"
    with pytest.raises(ValidationError):
        StatusCreate(name="   ", order=0)
    with pytest.raises(ValidationError):
        StatusCreate(name="Ready", order=-1)
    with pytest.raises(ValidationError):
        StatusCreate(name="x" * 51, order=0)


def test_status_update_normalizes_name_and_rejects_invalid_order():
    assert StatusUpdate(name="  Closed ").name == "Closed"
    with pytest.raises(ValidationError):
        StatusUpdate(name="   ")
    with pytest.raises(ValidationError):
        StatusUpdate(order=-1)


def test_ticket_update_uses_enums():
    update = TicketUpdate(priority=Priority.HIGH, category=Category.BUG)
    assert update.priority is Priority.HIGH
    assert update.category is Category.BUG
    with pytest.raises(ValidationError):
        TicketUpdate(priority="banana")
    with pytest.raises(ValidationError):
        TicketUpdate(category="not-real")


def test_ticket_text_limits():
    with pytest.raises(ValidationError):
        TicketUpdate(title="")
    with pytest.raises(ValidationError):
        TicketUpdate(title="x" * 256)
    with pytest.raises(ValidationError):
        TicketUpdate(description="x" * 10001)
