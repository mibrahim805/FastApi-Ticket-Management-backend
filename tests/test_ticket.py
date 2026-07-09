from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.api import ticket
from app.api.ticket import calculate_due_date
from app.models.ticket import AssignAgent, Category, Priority, TicketCreate


@pytest.mark.parametrize(
    "priority, days",
    [
        ("high", 1),
        ("medium", 3),
        ("low", 7),
    ],
)
def test_calculate_due_date(monkeypatch, priority, days):
    fixed_time = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)

    class FakeDateTime:
        @classmethod
        def now(cls, tz=None):
            return fixed_time

    monkeypatch.setattr("app.api.ticket.datetime", FakeDateTime)

    due_date = calculate_due_date(priority)

    assert due_date == fixed_time + timedelta(days=days)


def test_calculate_due_date_invalid_priority():
    with pytest.raises(ValueError):
        calculate_due_date("invalid")


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
    counters = fake_collection.__class__()

    monkeypatch.setattr(ticket, "boards_collection", boards)
    monkeypatch.setattr(ticket, "statuses_collection", statuses)
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "notifications_collection", notifications)
    monkeypatch.setattr(ticket, "counters_collection", counters)
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

    assert response["ticket_code"].startswith("TKT-")
    assert response["current_status_id"] == str(status_id)
    assert response["due_date"] == "due-date"
    assert len(tickets.documents) == 1
    assert len(notifications.documents) == 1


def test_create_ticket_rejects_missing_board(monkeypatch, fake_collection):
    monkeypatch.setattr(ticket, "boards_collection", fake_collection)
    monkeypatch.setattr(ticket, "statuses_collection", fake_collection.__class__())

    payload = TicketCreate(
        board_id=str(ObjectId()),
        title="Printer issue",
        description="Printer is offline",
        category=Category.SUPPORT,
        priority=Priority.HIGH,
    )

    with pytest.raises(HTTPException) as exc_info:
        ticket.create_ticket(payload, current_user={"_id": ObjectId(), "role": "agent"})

    assert exc_info.value.status_code == 404


def test_create_ticket_rejects_board_with_no_statuses(monkeypatch, fake_collection):
    board_id = ObjectId()
    boards = fake_collection.__class__(
        [
            {
                "_id": board_id,
                "code": "SUP",
                "assigned_agent": str(ObjectId()),
                "assigned_statuses": [],
            }
        ]
    )
    monkeypatch.setattr(ticket, "boards_collection", boards)
    monkeypatch.setattr(ticket, "statuses_collection", fake_collection.__class__())

    payload = TicketCreate(
        board_id=str(board_id),
        title="Printer issue",
        description="Printer is offline",
        category=Category.SUPPORT,
        priority=Priority.HIGH,
    )

    with pytest.raises(HTTPException) as exc_info:
        ticket.create_ticket(
            payload, current_user={"_id": ObjectId(), "role": "super_admin"}
        )

    assert exc_info.value.status_code == 400


def test_create_ticket_rejects_missing_first_status(monkeypatch, fake_collection):
    board_id = ObjectId()
    missing_status_id = ObjectId()
    boards = fake_collection.__class__(
        [
            {
                "_id": board_id,
                "code": "SUP",
                "assigned_agent": str(ObjectId()),
                "assigned_statuses": [str(missing_status_id)],
            }
        ]
    )
    monkeypatch.setattr(ticket, "boards_collection", boards)
    monkeypatch.setattr(ticket, "statuses_collection", fake_collection.__class__())

    payload = TicketCreate(
        board_id=str(board_id),
        title="Printer issue",
        description="Printer is offline",
        category=Category.SUPPORT,
        priority=Priority.HIGH,
    )

    with pytest.raises(HTTPException) as exc_info:
        ticket.create_ticket(
            payload, current_user={"_id": ObjectId(), "role": "super_admin"}
        )

    assert exc_info.value.status_code == 400


def test_create_ticket_rejects_invalid_board_id(monkeypatch, fake_collection):
    monkeypatch.setattr(ticket, "boards_collection", fake_collection)
    monkeypatch.setattr(ticket, "statuses_collection", fake_collection.__class__())

    payload = TicketCreate(
        board_id="invalid-board-id",
        title="Printer issue",
        description="Printer is offline",
        category=Category.SUPPORT,
        priority=Priority.HIGH,
    )

    with pytest.raises(HTTPException) as exc_info:
        ticket.create_ticket(payload, current_user={"_id": ObjectId(), "role": "agent"})

    assert exc_info.value.status_code == 400


def test_create_ticket_allows_any_authenticated_user(monkeypatch, fake_collection):
    board_id = ObjectId()
    status_id = ObjectId()
    boards = fake_collection.__class__(
        [
            {
                "_id": board_id,
                "code": "SUP",
                "assigned_agent": str(ObjectId()),
                "assigned_statuses": [str(status_id)],
            }
        ]
    )
    statuses = fake_collection.__class__([{"_id": status_id, "name": "Open"}])
    tickets = fake_collection.__class__()
    notifications = fake_collection.__class__()
    counters = fake_collection.__class__()

    monkeypatch.setattr(ticket, "boards_collection", boards)
    monkeypatch.setattr(ticket, "statuses_collection", statuses)
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "notifications_collection", notifications)
    monkeypatch.setattr(ticket, "counters_collection", counters)
    monkeypatch.setattr(ticket, "log_activity", lambda **kwargs: None)
    monkeypatch.setattr(ticket, "calculate_due_date", lambda priority: "due-date")

    payload = TicketCreate(
        board_id=str(board_id),
        title="Printer issue",
        description="Printer is offline",
        category=Category.SUPPORT,
        priority=Priority.HIGH,
    )

    response = ticket.create_ticket(
        payload, current_user={"_id": ObjectId(), "role": "agent"}
    )

    assert response["ticket_code"].startswith("TKT-")
    assert len(tickets.documents) == 1


def test_get_tickets_includes_tickets_assigned_to_current_user(
    monkeypatch, fake_collection
):
    board_code = "SUPPORT"
    current_user_id = ObjectId()
    other_user_id = ObjectId()

    boards = fake_collection.__class__([{"_id": ObjectId(), "code": board_code}])
    tickets = fake_collection.__class__(
        [
            {
                "_id": ObjectId(),
                "board_code": board_code,
                "ticket_code": "TKT-1",
                "userId": str(current_user_id),
                "assigned_agent": None,
                "title": "Created by me",
                "created_date": datetime(2026, 7, 1, tzinfo=timezone.utc),
            },
            {
                "_id": ObjectId(),
                "board_code": board_code,
                "ticket_code": "TKT-2",
                "userId": str(other_user_id),
                "assigned_agent": str(current_user_id),
                "title": "Assigned to me",
                "created_date": datetime(2026, 7, 2, tzinfo=timezone.utc),
            },
            {
                "_id": ObjectId(),
                "board_code": board_code,
                "ticket_code": "TKT-3",
                "userId": str(other_user_id),
                "assigned_agent": str(other_user_id),
                "title": "Not mine",
                "created_date": datetime(2026, 7, 3, tzinfo=timezone.utc),
            },
        ]
    )

    monkeypatch.setattr(ticket, "boards_collection", boards)
    monkeypatch.setattr(ticket, "ticket_collection", tickets)

    response = ticket.get_tickets(
        board_code,
        page=1,
        page_size=10,
        status=None,
        priority=None,
        category=None,
        assigned_agent=None,
        search=None,
        overdue_only=None,
        sort_by="created_at",
        sort_order="desc",
        current_user={"_id": current_user_id, "role": "agent"},
    )

    returned_codes = {item["ticket_code"] for item in response["data"]}
    assert returned_codes == {"TKT-1", "TKT-2"}
    assert response["pagination"]["total"] == 2


def test_get_tickets_handles_naive_due_date(monkeypatch, fake_collection):
    board_code = "SUPPORT"
    current_user_id = ObjectId()

    boards = fake_collection.__class__([{"_id": ObjectId(), "code": board_code}])
    tickets = fake_collection.__class__(
        [
            {
                "_id": ObjectId(),
                "board_code": board_code,
                "ticket_code": "TKT-1",
                "userId": str(current_user_id),
                "assigned_agent": None,
                "title": "Naive due date",
                "current_status_name": "open",
                "created_date": datetime(2026, 7, 1),
                "due_date": datetime(2020, 1, 1),
            }
        ]
    )

    monkeypatch.setattr(ticket, "boards_collection", boards)
    monkeypatch.setattr(ticket, "ticket_collection", tickets)

    response = ticket.get_tickets(
        board_code,
        page=1,
        page_size=10,
        status=None,
        priority=None,
        category=None,
        assigned_agent=None,
        search=None,
        overdue_only=None,
        sort_by="created_at",
        sort_order="desc",
        current_user={"_id": current_user_id, "role": "agent"},
    )

    assert response["data"][0]["is_overdue"] is True


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


def test_move_ticket_forward_rejects_last_status(monkeypatch, fake_collection):
    board_id = ObjectId()
    open_id = ObjectId()
    progress_id = ObjectId()
    ticket_id = ObjectId()
    current_user = {"_id": ObjectId(), "role": "agent"}

    boards = fake_collection.__class__(
        [
            {
                "_id": board_id,
                "assigned_statuses": [str(open_id), str(progress_id)],
            }
        ]
    )
    tickets = fake_collection.__class__(
        [
            {
                "_id": ticket_id,
                "board_id": str(board_id),
                "userId": str(current_user["_id"]),
                "ticket_code": "SUP-3",
                "current_status_id": str(progress_id),
                "current_status_name": "In Progress",
            }
        ]
    )

    monkeypatch.setattr(ticket, "boards_collection", boards)
    monkeypatch.setattr(ticket, "statuses_collection", fake_collection.__class__())
    monkeypatch.setattr(ticket, "ticket_collection", tickets)

    with pytest.raises(HTTPException) as exc_info:
        ticket.move_ticket_forward(str(ticket_id), current_user=current_user)

    assert exc_info.value.status_code == 400


def test_move_ticket_forward_rejects_missing_ticket(monkeypatch, fake_collection):
    monkeypatch.setattr(ticket, "ticket_collection", fake_collection)

    with pytest.raises(HTTPException) as exc_info:
        ticket.move_ticket_forward(
            str(ObjectId()), current_user={"_id": ObjectId(), "role": "agent"}
        )

    assert exc_info.value.status_code == 404


def test_move_ticket_forward_rejects_invalid_ticket_id(monkeypatch, fake_collection):
    monkeypatch.setattr(ticket, "ticket_collection", fake_collection)

    with pytest.raises(HTTPException) as exc_info:
        ticket.move_ticket_forward(
            "invalid-ticket-id", current_user={"_id": ObjectId(), "role": "agent"}
        )

    assert exc_info.value.status_code == 400


def test_move_ticket_forward_rejects_board_not_found(monkeypatch, fake_collection):
    ticket_id = ObjectId()
    tickets = fake_collection.__class__(
        [
            {
                "_id": ticket_id,
                "board_id": str(ObjectId()),
                "userId": str(ObjectId()),
                "ticket_code": "SUP-3",
                "current_status_id": str(ObjectId()),
                "current_status_name": "Open",
            }
        ]
    )
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "boards_collection", fake_collection.__class__())

    with pytest.raises(HTTPException) as exc_info:
        ticket.move_ticket_forward(
            str(ticket_id), current_user={"_id": ObjectId(), "role": "super_admin"}
        )

    assert exc_info.value.status_code == 404


def test_move_ticket_forward_rejects_status_not_in_board(monkeypatch, fake_collection):
    board_id = ObjectId()
    ticket_id = ObjectId()
    tickets = fake_collection.__class__(
        [
            {
                "_id": ticket_id,
                "board_id": str(board_id),
                "userId": str(ObjectId()),
                "ticket_code": "SUP-3",
                "current_status_id": str(ObjectId()),
                "current_status_name": "Open",
            }
        ]
    )
    boards = fake_collection.__class__(
        [{"_id": board_id, "assigned_statuses": [str(ObjectId()), str(ObjectId())]}]
    )
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "boards_collection", boards)

    with pytest.raises(HTTPException) as exc_info:
        ticket.move_ticket_forward(
            str(ticket_id), current_user={"_id": ObjectId(), "role": "super_admin"}
        )

    assert exc_info.value.status_code == 400


def test_move_ticket_forward_rejects_missing_next_status(monkeypatch, fake_collection):
    board_id = ObjectId()
    current_status = ObjectId()
    next_status = ObjectId()
    ticket_id = ObjectId()
    tickets = fake_collection.__class__(
        [
            {
                "_id": ticket_id,
                "board_id": str(board_id),
                "userId": str(ObjectId()),
                "ticket_code": "SUP-3",
                "current_status_id": str(current_status),
                "current_status_name": "Open",
            }
        ]
    )
    boards = fake_collection.__class__(
        [
            {
                "_id": board_id,
                "assigned_statuses": [
                    str(current_status),
                    str(next_status),
                    str(ObjectId()),
                ],
            }
        ]
    )
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "boards_collection", boards)
    monkeypatch.setattr(ticket, "statuses_collection", fake_collection.__class__())

    with pytest.raises(HTTPException) as exc_info:
        ticket.move_ticket_forward(
            str(ticket_id), current_user={"_id": ObjectId(), "role": "super_admin"}
        )

    assert exc_info.value.status_code == 404


def test_assign_ticket_allows_agent_self_assign(monkeypatch, fake_collection):
    ticket_id = ObjectId()
    agent_id = ObjectId()
    tickets = fake_collection.__class__(
        [{"_id": ticket_id, "assigned_agent": None, "userId": "user"}]
    )
    users = fake_collection.__class__(
        [{"_id": agent_id, "role": "agent", "is_active": True}]
    )
    notifications = fake_collection.__class__()
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "user_collection", users)
    monkeypatch.setattr(ticket, "notifications_collection", notifications)

    response = ticket.assign_ticket(
        str(ticket_id),
        AssignAgent(agent_id=str(agent_id)),
        current_user={"_id": agent_id, "role": "agent"},
    )

    assert response == {"message": "Ticket assigned successfully"}
    assert tickets.documents[0]["assigned_agent"] == str(agent_id)
    assert len(notifications.documents) == 1


def test_assign_ticket_succeeds(monkeypatch, fake_collection):
    ticket_id = ObjectId()
    agent_id = ObjectId()
    tickets = fake_collection.__class__(
        [{"_id": ticket_id, "assigned_agent": None, "userId": "user"}]
    )
    users = fake_collection.__class__(
        [{"_id": agent_id, "role": "agent", "is_active": True}]
    )
    notifications = fake_collection.__class__()
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "user_collection", users)
    monkeypatch.setattr(ticket, "notifications_collection", notifications)

    response = ticket.assign_ticket(
        str(ticket_id),
        AssignAgent(agent_id=str(agent_id)),
        current_user={"_id": ObjectId(), "role": "super_admin"},
    )

    assert response == {"message": "Ticket assigned successfully"}
    assert tickets.documents[0]["assigned_agent"] == str(agent_id)
    assert len(notifications.documents) == 1


def test_assign_ticket_rejects_missing_ticket(monkeypatch, fake_collection):
    monkeypatch.setattr(ticket, "ticket_collection", fake_collection)
    monkeypatch.setattr(ticket, "user_collection", fake_collection.__class__())

    with pytest.raises(HTTPException) as exc_info:
        ticket.assign_ticket(
            str(ObjectId()),
            AssignAgent(agent_id=str(ObjectId())),
            current_user={"_id": ObjectId(), "role": "super_admin"},
        )

    assert exc_info.value.status_code == 404


def test_assign_ticket_rejects_missing_agent(monkeypatch, fake_collection):
    ticket_id = ObjectId()
    tickets = fake_collection.__class__(
        [{"_id": ticket_id, "assigned_agent": None, "userId": "user"}]
    )
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "user_collection", fake_collection.__class__())

    with pytest.raises(HTTPException) as exc_info:
        ticket.assign_ticket(
            str(ticket_id),
            AssignAgent(agent_id=str(ObjectId())),
            current_user={"_id": ObjectId(), "role": "super_admin"},
        )

    assert exc_info.value.status_code == 404


def test_assign_ticket_rejects_target_user_not_agent(monkeypatch, fake_collection):
    ticket_id = ObjectId()
    user_id = ObjectId()
    tickets = fake_collection.__class__(
        [{"_id": ticket_id, "assigned_agent": None, "userId": "user"}]
    )
    users = fake_collection.__class__(
        [{"_id": user_id, "role": "admin", "is_active": True}]
    )
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "user_collection", users)

    with pytest.raises(HTTPException) as exc_info:
        ticket.assign_ticket(
            str(ticket_id),
            AssignAgent(agent_id=str(user_id)),
            current_user={"_id": ObjectId(), "role": "super_admin"},
        )

    assert exc_info.value.status_code == 400


def test_assign_ticket_allows_admin_reassignment(monkeypatch, fake_collection):
    ticket_id = ObjectId()
    old_agent_id = ObjectId()
    new_agent_id = ObjectId()
    admin_id = ObjectId()
    tickets = fake_collection.__class__(
        [{"_id": ticket_id, "assigned_agent": str(old_agent_id), "userId": "user"}]
    )
    users = fake_collection.__class__(
        [
            {"_id": old_agent_id, "role": "agent", "is_active": True},
            {"_id": new_agent_id, "role": "agent", "is_active": True},
        ]
    )
    notifications = fake_collection.__class__()
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "user_collection", users)
    monkeypatch.setattr(ticket, "notifications_collection", notifications)

    response = ticket.assign_ticket(
        str(ticket_id),
        AssignAgent(agent_id=str(new_agent_id)),
        current_user={"_id": admin_id, "role": "super_admin"},
    )

    assert response == {"message": "Ticket assigned successfully"}
    assert tickets.documents[0]["assigned_agent"] == str(new_agent_id)
    assert len(notifications.documents) == 1
