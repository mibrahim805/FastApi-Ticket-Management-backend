from datetime import datetime, timezone

import pytest
from bson import ObjectId
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import JWTError

from app.api import activity, auth
from app.api import board as board_mod
from app.api import status as status_mod
from app.api import ticket as ticket_mod
from app.models.status import StatusCreate, StatusUpdate
from app.models.ticket import TicketUpdate


def test_activity_get_ticket_activity_not_found(monkeypatch, fake_collection):
    tickets = fake_collection.__class__()
    monkeypatch.setattr(activity, "ticket_collection", tickets)

    with pytest.raises(HTTPException) as exc:
        activity.get_ticket_activity(str(ObjectId()))
    assert exc.value.status_code == 404


def test_activity_get_ticket_activity_success(monkeypatch, fake_collection):
    ticket_id = str(ObjectId())
    t = {"_id": ObjectId(ticket_id), "ticket_code": "T-1"}
    tickets = fake_collection.__class__([t])
    acts = fake_collection.__class__(
        [
            {
                "ticket_id": ticket_id,
                "changed_by": ObjectId(),
                "changed_at": datetime.now(timezone.utc),
            }
        ]
    )
    monkeypatch.setattr(activity, "ticket_collection", tickets)
    monkeypatch.setattr(activity, "ticket_activity_collection", acts)

    resp = activity.get_ticket_activity(ticket_id)
    assert "ticket_id" in resp.body.decode()
    assert ticket_id in resp.body.decode()


def test_get_current_user_invalid_token(monkeypatch):
    # jwt.decode raises JWTError
    monkeypatch.setattr(auth, "security_scheme", lambda: None)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="bad")
    monkeypatch.setattr(auth, "jwt", auth.jwt)
    monkeypatch.setattr(auth, "jwt", auth.jwt)
    monkeypatch.setattr(
        auth.jwt, "decode", lambda *a, **k: (_ for _ in ()).throw(JWTError())
    )

    with pytest.raises(HTTPException) as exc:
        auth.get_current_user(token=creds)
    assert exc.value.status_code == 401


def test_get_current_user_user_not_found(monkeypatch, fake_collection):
    # jwt.decode returns sub but user not found
    monkeypatch.setattr(auth, "jwt", auth.jwt)
    monkeypatch.setattr(
        auth.jwt, "decode", lambda token, s, algorithms: {"sub": str(ObjectId())}
    )
    fake_users = fake_collection.__class__()
    monkeypatch.setattr(auth, "user_collection", fake_users)

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="tok")
    with pytest.raises(HTTPException) as exc:
        auth.get_current_user(token=creds)
    assert exc.value.status_code == 404


def test_status_create_and_update_and_delete(monkeypatch, fake_collection):
    statuses = fake_collection.__class__()
    boards = fake_collection.__class__()
    monkeypatch.setattr(status_mod, "statuses_collection", statuses)
    monkeypatch.setattr(status_mod, "boards_collection", boards)

    # create
    resp = status_mod.create_status(
        StatusCreate(name="New", order=10), current_user={"role": "super_admin"}
    )
    assert resp["name"] == "New"

    # duplicate name
    statuses.insert_one({"name": "Dup", "order": 1})
    with pytest.raises(HTTPException):
        status_mod.create_status(
            StatusCreate(name="Dup", order=2), current_user={"role": "super_admin"}
        )

    # duplicate order
    statuses.insert_one({"name": "Other", "order": 10})
    with pytest.raises(HTTPException):
        status_mod.create_status(
            StatusCreate(name="Another", order=10), current_user={"role": "super_admin"}
        )

    # list
    lst = status_mod.list_statuses(current_user={"role": "super_admin"})
    assert isinstance(lst, list)

    # update missing
    with pytest.raises(HTTPException):
        status_mod.update_status(
            str(ObjectId()),
            StatusUpdate(name="X"),
            current_user={"role": "super_admin"},
        )

    # insert one and update
    sid = statuses.insert_one({"name": "Up", "order": 5}).inserted_id
    with pytest.raises(HTTPException):
        status_mod.update_status(
            str(sid), StatusUpdate(), current_user={"role": "super_admin"}
        )

    resp2 = status_mod.update_status(
        str(sid), StatusUpdate(name="Updated"), current_user={"role": "super_admin"}
    )
    assert resp2["message"] == "Status updated successfully"

    # delete: assigned to board
    boards.insert_one({"assigned_statuses": str(sid)}).inserted_id
    with pytest.raises(HTTPException):
        status_mod.delete_status(str(sid), current_user={"role": "super_admin"})

    # remove assignment and delete
    boards.documents = []
    resp3 = status_mod.delete_status(str(sid), current_user={"role": "super_admin"})
    assert resp3["message"] == "Status deleted successfully"


def test_board_get_update_delete(monkeypatch, fake_collection):
    boards = fake_collection.__class__()
    tickets = fake_collection.__class__()
    monkeypatch.setattr(board_mod, "boards_collection", boards)
    monkeypatch.setattr(board_mod, "ticket_collection", tickets)

    # create
    from app.models.board import BoardCreate

    resp = board_mod.create_board(
        BoardCreate(name="B", code="BC"), current_user={"role": "super_admin"}
    )
    assert "id" in resp

    # get_boards
    boards.documents.append({"_id": ObjectId(), "code": "C1"})
    gb = board_mod.get_boards(current_user={"role": "super_admin"})
    assert "boards" in gb

    # get_board not found
    with pytest.raises(HTTPException):
        board_mod.get_board(
            str(ObjectId()), current_user={"_id": ObjectId(), "role": "super_admin"}
        )

    # insert and unauthorized view (assigned_agent mismatch)
    different_agent_id = ObjectId()
    boards.insert_one(
        {"_id": ObjectId(), "assigned_agent": str(different_agent_id)}
    ).inserted_id
    result = board_mod.get_board(
        str(boards.documents[-1]["_id"]),
        current_user={"_id": ObjectId(), "role": "agent"},
    )
    assert "message" in result

    # update
    with pytest.raises(HTTPException):
        board_mod.update_board(
            {"name": "X"}, str(ObjectId()), current_user={"role": "super_admin"}
        )

    # delete not found
    with pytest.raises(HTTPException):
        board_mod.delete_board(str(ObjectId()), current_user={"role": "super_admin"})


def test_ticket_get_update_summary_delete(monkeypatch, fake_collection):
    tickets = fake_collection.__class__()
    comments = fake_collection.__class__()
    notifications = fake_collection.__class__()
    boards = fake_collection.__class__()
    statuses = fake_collection.__class__()
    users = fake_collection.__class__()

    monkeypatch.setattr(ticket_mod, "ticket_collection", tickets)
    monkeypatch.setattr(ticket_mod, "comments_collection", comments)
    monkeypatch.setattr(ticket_mod, "notifications_collection", notifications)
    monkeypatch.setattr(ticket_mod, "boards_collection", boards)
    monkeypatch.setattr(ticket_mod, "statuses_collection", statuses)
    monkeypatch.setattr(ticket_mod, "user_collection", users)
    monkeypatch.setattr(ticket_mod, "log_activity", lambda **k: None)

    # get details missing
    with pytest.raises(HTTPException):
        ticket_mod.get_ticket_details(
            current_user={"_id": ObjectId(), "role": "agent"}, ticket_id=str(ObjectId())
        )

    # insert ticket and comments and get details unauthorized
    tid = tickets.insert_one({"_id": ObjectId(), "userId": str(ObjectId())}).inserted_id
    with pytest.raises(HTTPException):
        ticket_mod.get_ticket_details(
            current_user={"_id": ObjectId(), "role": "agent"}, ticket_id=str(tid)
        )

    # now as super_admin
    resp = ticket_mod.get_ticket_details(
        current_user={"_id": ObjectId(), "role": "super_admin"}, ticket_id=str(tid)
    )
    assert "tickets" in resp

    # update ticket
    # create ticket with fields
    t_owner = ObjectId()
    tid2 = tickets.insert_one(
        {
            "_id": ObjectId(),
            "userId": str(t_owner),
            "title": "T",
            "description": "D",
            "priority": "low",
            "status": "open",
            "category": "support",
            "assigned_agent": str(ObjectId()),
        }
    ).inserted_id
    upd = TicketUpdate(
        status="in_progress", priority="high", category="bug", assigned_to="agent"
    )
    resp2 = ticket_mod.update_ticket(
        str(tid2), upd, current_user={"_id": t_owner, "role": "agent"}
    )
    assert resp2["message"] == "Ticket updated successfully"

    # get_tickets with board not found
    with pytest.raises(HTTPException):
        ticket_mod.get_tickets(
            "NOBOARD", current_user={"_id": ObjectId(), "role": "agent"}
        )

    # create board and tickets and test pagination and filters
    bcode = "BC"
    boards.insert_one({"_id": ObjectId(), "code": bcode})
    tickets.insert_one(
        {
            "_id": ObjectId(),
            "board_code": bcode,
            "createdAt": datetime.now(),
            "priorityOrder": 1,
            "dueDate": datetime.now(),
            "userId": str(ObjectId()),
        }
    )
    res = ticket_mod.get_tickets(
        bcode,
        page=1,
        page_size=1,
        status=None,
        priority=None,
        category=None,
        assigned_agent=None,
        search=None,
        sort_by="created_at",
        sort_order="desc",
        current_user={"_id": ObjectId(), "role": "super_admin"},
    )
    assert "data" in res

    # summary empty
    summ = ticket_mod.get_tickets_summary(
        current_user={"_id": ObjectId(), "role": "super_admin"}
    )
    assert isinstance(summ, dict)

    # delete ticket not found
    with pytest.raises(HTTPException):
        ticket_mod.delete_ticket(
            str(ObjectId()), current_user={"_id": ObjectId(), "role": "agent"}
        )

    # insert and delete success
    td = tickets.insert_one({"_id": ObjectId(), "userId": str(ObjectId())}).inserted_id
    resp3 = ticket_mod.delete_ticket(
        str(td), current_user={"_id": ObjectId(), "role": "super_admin"}
    )
    assert resp3["message"] == "Ticket deleted successfully"


def test_super_admin_decorator_behaviour():
    from app.decorator.super_admin import super_admin_required

    @super_admin_required
    def fn(current_user=None):
        return "ok"

    with pytest.raises(HTTPException):
        fn()

    with pytest.raises(HTTPException):
        fn(current_user={"role": "agent"})

    assert fn(current_user={"role": "super_admin"}) == "ok"
