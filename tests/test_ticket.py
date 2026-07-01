import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.api import ticket
from app.models.ticket import AssignAgent, Category, Priority, TicketCreate


def test_create_ticket_assigns_first_status_and_updates_counter(
    monkeypatch, fake_collection
):
    board_id = ObjectId()
    status_id = ObjectId()
    current_user = {"_id": ObjectId(), "role": "agent"}

    boards = fake_collection.__class__(
        [
            {
                "_id": board_id,
                "code": "SUP",
                "assigned_agent": str(current_user["_id"]),
                "assigned_statuses": [str(status_id)],
                "ticket_counter": 2,
            }
        ]
    )
    statuses = fake_collection.__class__([{"_id": status_id, "name": "Open"}])
    tickets = fake_collection.__class__()
    notifications = fake_collection.__class__()

    monkeypatch.setattr(ticket, "boards_collection", boards)
    monkeypatch.setattr(ticket, "statuses_collection", statuses)
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "notifications_collection", notifications)
    monkeypatch.setattr(ticket, "log_activity", lambda **kwargs: None)
    monkeypatch.setattr(ticket, "calculate_due_date", lambda priority: "due-date")

    payload = TicketCreate(
        board_id=str(board_id),
        title="Printer issue",
        description="Printer is offline",
        category=Category.SUPPORT,
        priority=Priority.HIGH,
    )

    response = ticket.create_ticket(payload, current_user=current_user)

    assert response["ticket_code"] == "SUP-3"
    assert response["current_status_id"] == str(status_id)
    assert response["due_date"] == "due-date"
    assert boards.documents[0]["ticket_counter"] == 3
    assert len(tickets.documents) == 1
    assert len(notifications.documents) == 1


def test_move_ticket_forward_advances_status(monkeypatch, fake_collection):
    board_id = ObjectId()
    open_id = ObjectId()
    progress_id = ObjectId()
    closed_id = ObjectId()
    ticket_id = ObjectId()
    current_user = {"_id": ObjectId(), "role": "agent"}

    boards = fake_collection.__class__(
        [
            {
                "_id": board_id,
                "assigned_statuses": [str(open_id), str(progress_id), str(closed_id)],
            }
        ]
    )
    statuses = fake_collection.__class__(
        [
            {"_id": progress_id, "name": "In Progress"},
        ]
    )
    tickets = fake_collection.__class__(
        [
            {
                "_id": ticket_id,
                "board_id": str(board_id),
                "userId": str(current_user["_id"]),
                "ticket_code": "SUP-3",
                "current_status_id": str(open_id),
                "current_status_name": "Open",
            }
        ]
    )

    monkeypatch.setattr(ticket, "boards_collection", boards)
    monkeypatch.setattr(ticket, "statuses_collection", statuses)
    monkeypatch.setattr(ticket, "ticket_collection", tickets)

    response = ticket.move_ticket_forward(str(ticket_id), current_user=current_user)

    assert response["previous_status"] == "Open"
    assert response["current_status"] == "In Progress"
    assert tickets.documents[0]["current_status_name"] == "In Progress"


def test_assign_ticket_requires_super_admin(monkeypatch, fake_collection):
    tickets = fake_collection.__class__(
        [{"_id": ObjectId(), "assigned_agent": None, "userId": "user"}]
    )
    monkeypatch.setattr(ticket, "ticket_collection", tickets)

    with pytest.raises(HTTPException) as exc_info:
        ticket.assign_ticket(
            str(tickets.documents[0]["_id"]),
            AssignAgent(agent_id="agent-1"),
            current_user={"_id": ObjectId(), "role": "agent"},
        )

    assert exc_info.value.status_code == 403
