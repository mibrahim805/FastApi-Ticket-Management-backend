from datetime import datetime, timezone

import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.api import activity, auth
from app.api import board as board_mod
from app.api import status as status_mod
from app.api import ticket as ticket_mod
from app.models.board import AssignBoard
from app.models.status import StatusUpdate
from app.models.ticket import TicketUpdate


def test_activity_log_and_conversion(monkeypatch, fake_collection, grant_board_role):
    # ensure log_activity inserts and get_ticket_activity converts ObjectId and datetime
    acts = fake_collection.__class__()
    board_id = ObjectId()
    user_id = ObjectId()
    tickets = fake_collection.__class__(
        [{"_id": ObjectId(), "board_id": str(board_id), "ticket_code": "X-1"}]
    )
    monkeypatch.setattr(activity, "ticket_activity_collection", acts)
    monkeypatch.setattr(activity, "ticket_collection", tickets)

    # create a predictable activity and patch find to return it
    # (get_ticket_activity uses $in which FakeCollection doesn't support)

    act = {
        "ticket_id": str(tickets.documents[0]["_id"]),
        "changed_by": ObjectId(),
        "changed_at": datetime.now(timezone.utc),
    }

    def simple_find(q):
        class C:
            def __init__(self, docs):
                self._docs = docs

            def sort(self, *a, **k):
                return self

            def __iter__(self):
                return iter(self._docs)

        return C([act])

    monkeypatch.setattr(
        activity,
        "ticket_activity_collection",
        type("X", (), {"find": staticmethod(simple_find)}),
    )
    grant_board_role(board_id, user_id, "member")
    resp = activity.get_ticket_activity(
        str(tickets.documents[0]["_id"]),
        current_user={"_id": user_id, "role": "user"},
    )
    body = resp.body.decode()
    assert "X-1" in body
    assert "changed_by" in body


def test_auth_hash_and_token_and_generate(monkeypatch):
    pwd = "secret"
    hashed = auth.hash_password(pwd)
    assert auth.verify_password(pwd, hashed) is True
    assert auth.verify_password("wrong", hashed) is False

    # create access token and decode
    token = auth.create_access_token({"sub": "123"})
    claims = auth.jwt.decode(token, auth.SECRET_KEY, algorithms=[auth.ALGORITHM])
    assert claims["sub"] == "123"
    assert "exp" in claims

    # generate reset token
    t = auth.generate_reset_token()
    assert isinstance(t, str) and len(t) > 0


def test_board_assign_update_delete_success(
    monkeypatch, fake_collection, grant_board_role
):
    boards = fake_collection.__class__()
    tickets = fake_collection.__class__()
    monkeypatch.setattr(board_mod, "boards_collection", boards)
    monkeypatch.setattr(board_mod, "ticket_collection", tickets)

    # assign_board success when no assigned_agent
    bid = boards.insert_one({"_id": ObjectId(), "assigned_agent": None}).inserted_id
    lead_id = ObjectId()
    agent1_id = ObjectId()
    agent2_id = ObjectId()
    grant_board_role(bid, lead_id)
    grant_board_role(bid, agent1_id, "member")
    grant_board_role(bid, agent2_id, "member")

    resp = board_mod.assign_board(
        str(bid),
        AssignBoard(agent_id=str(agent1_id)),
        current_user={"_id": lead_id, "role": "super_admin"},
    )
    assert resp["message"] == "Board assigned to agent"
    assert boards.documents[-1]["assigned_agent"] == str(agent1_id)

    # reassignment allowed for super_admin
    resp2 = board_mod.assign_board(
        str(bid),
        AssignBoard(agent_id=str(agent2_id)),
        current_user={"_id": lead_id, "role": "super_admin"},
    )

    assert resp2["message"] == "Board assigned to agent"
    assert boards.documents[-1]["assigned_agent"] == str(agent2_id)

    # update_board success
    boards.insert_one({"_id": ObjectId(), "name": "Old"})
    uid = str(boards.documents[-1]["_id"])
    grant_board_role(uid, lead_id)
    from app.models.board import BoardCreate

    resp3 = board_mod.update_board(
        BoardCreate(name="New", code="C"),
        uid,
        current_user={"_id": lead_id, "role": "super_admin"},
    )

    assert resp3["message"] == "Board updated"

    # get_board success when assigned_agent matches
    # test by setting assigned_agent to current_user
    uid2 = ObjectId()
    boards.insert_one({"_id": ObjectId(), "assigned_agent": str(uid2)})
    grant_board_role(boards.documents[-1]["_id"], uid2, "reporter")
    res_get = board_mod.get_board(
        str(boards.documents[-1]["_id"]),
        current_user={"_id": uid2, "role": "super_admin"},
    )
    assert "board" in res_get

    # delete success
    d = boards.insert_one({"_id": ObjectId()}).inserted_id
    grant_board_role(d, lead_id)
    resp4 = board_mod.delete_board(
        str(d), current_user={"_id": lead_id, "role": "super_admin"}
    )
    assert resp4["message"] == "Board deleted"


def test_assign_statuses_to_board_too_few(
    monkeypatch, fake_collection, grant_board_role
):
    boards = fake_collection.__class__([{"_id": ObjectId()}])
    statuses = fake_collection.__class__()
    monkeypatch.setattr(board_mod, "boards_collection", boards)
    monkeypatch.setattr(board_mod, "statuses_collection", statuses)

    from app.models.board import BoardStatusAssign

    lead_id = ObjectId()
    grant_board_role(boards.documents[0]["_id"], lead_id)

    with pytest.raises(HTTPException):
        board_mod.assign_statuses_to_board(
            str(boards.documents[0]["_id"]),
            BoardStatusAssign(status_ids=[str(ObjectId())]),
            current_user={"_id": lead_id, "role": "super_admin"},
        )


def test_ticket_update_missing_and_unauthorized_and_filters(
    monkeypatch, fake_collection, grant_board_role
):
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

    # update missing
    with pytest.raises(HTTPException):
        ticket_mod.update_ticket(
            str(ObjectId()),
            TicketUpdate(status="s", priority="p", category="c", assigned_to="a"),
            current_user={"_id": ObjectId(), "role": "agent"},
        )

    # insert and unauthorized update
    tid = tickets.insert_one({"_id": ObjectId(), "userId": "owner"}).inserted_id
    with pytest.raises(HTTPException):
        ticket_mod.update_ticket(
            str(tid),
            TicketUpdate(status="s", priority="p", category="c", assigned_to="a"),
            current_user={"_id": ObjectId(), "role": "agent"},
        )

    # get_tickets with filters: status/priority/search/sort asc and non-super_admin
    bcode = "BCX"
    board_id = boards.insert_one({"_id": ObjectId(), "code": bcode}).inserted_id
    uid = ObjectId()
    grant_board_role(board_id, uid, "member")
    tickets.insert_one(
        {
            "_id": ObjectId(),
            "board_code": bcode,
            "createdAt": datetime.now(timezone.utc),
            "priorityOrder": 1,
            "dueDate": datetime.now(timezone.utc),
            "userId": str(uid),
            "title": "FindMe",
            "ticketNumber": "TN1",
            "status": "Open",
            "priority": "High",
        }
    )

    res = ticket_mod.get_tickets(
        bcode,
        page=1,
        page_size=10,
        status="Open",
        priority="High",
        category=None,
        assigned_agent=None,
        search="FindMe",
        sort_by="priority",
        sort_order="asc",
        current_user={"_id": uid, "role": "agent"},
    )
    assert "data" in res

    # summary non-super_admin
    summ = ticket_mod.get_tickets_summary(current_user={"_id": uid, "role": "agent"})
    assert isinstance(summ, dict)

    # delete unauthorized
    td = tickets.insert_one({"_id": ObjectId(), "userId": "ownerX"}).inserted_id
    with pytest.raises(HTTPException):
        ticket_mod.delete_ticket(
            str(td), current_user={"_id": ObjectId(), "role": "agent"}
        )

    # move_ticket_forward: board has no statuses
    t1 = tickets.insert_one(
        {
            "_id": ObjectId(),
            "board_id": str(ObjectId()),
            "userId": str(uid),
            "ticket_code": "T1",
            "current_status_id": str(ObjectId()),
            "current_status_name": "S",
        }
    ).inserted_id
    boards.insert_one({"_id": ObjectId()})
    with pytest.raises(HTTPException):
        ticket_mod.move_ticket_forward(
            str(t1), current_user={"_id": uid, "role": "super_admin"}
        )

    # additional branches: get_tickets category and assigned_agent
    tickets.insert_one(
        {
            "_id": ObjectId(),
            "board_code": bcode,
            "createdAt": datetime.now(timezone.utc),
            "priorityOrder": 1,
            "dueDate": datetime.now(timezone.utc),
            "userId": str(uid),
            "category": "support",
            "assigned_agent": "ag1",
        }
    )
    res_f = ticket_mod.get_tickets(
        bcode,
        page=1,
        page_size=10,
        status=None,
        priority=None,
        category="support",
        assigned_agent="ag1",
        search=None,
        sort_by="created_at",
        sort_order="desc",
        current_user={"_id": uid, "role": "super_admin"},
    )
    assert "data" in res_f

    # move_ticket_forward board has empty statuses -> 400
    b_empty = boards.insert_one(
        {"_id": ObjectId(), "assigned_statuses": []}
    ).inserted_id
    grant_board_role(b_empty, uid, "member")
    tt_empty = tickets.insert_one(
        {
            "_id": ObjectId(),
            "board_id": str(b_empty),
            "userId": str(uid),
            "ticket_code": "T_EMPTY",
            "current_status_id": str(ObjectId()),
            "current_status_name": "S",
        }
    ).inserted_id
    with pytest.raises(HTTPException):
        ticket_mod.move_ticket_forward(
            str(tt_empty), current_user={"_id": uid, "role": "super_admin"}
        )


# registration extra branches
def test_register_admin_or_agent_cannot_create_super_admin(
    monkeypatch, fake_collection
):
    from app.api import registration

    users = fake_collection.__class__()
    monkeypatch.setattr(registration, "user_collection", users)

    payload = registration.UserRegister(
        name="X",
        email="x@example.com",
        password="p",
        role="super_admin",
    )

    with pytest.raises(HTTPException) as exc_info:
        registration.register_admin_or_agent(
            payload,
            current_user={"role": "super_admin"},
        )

    assert exc_info.value.status_code == 400


def test_activate_or_deactivate_activates(monkeypatch, fake_collection):
    from app.api import registration

    users = fake_collection.__class__()
    monkeypatch.setattr(registration, "user_collection", users)

    uid = ObjectId()
    users.insert_one({"_id": uid, "role": "user", "is_active": False})
    resp = registration.activate_or_deactivate(
        str(uid), current_user={"role": "super_admin"}
    )
    assert resp == {"message": "User activated successfully"}


def test_ticket_comments_conversion_and_move_last_closes(
    monkeypatch, fake_collection, grant_board_role
):
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

    # comments conversion
    detail_board_id = ObjectId()
    detail_user_id = ObjectId()
    tid = tickets.insert_one(
        {"_id": ObjectId(), "board_id": str(detail_board_id), "userId": "u"}
    ).inserted_id
    grant_board_role(detail_board_id, detail_user_id)
    comments.insert_one({"_id": ObjectId(), "ticket_id": str(tid), "text": "c"})
    resp = ticket_mod.get_ticket_details(
        current_user={"_id": detail_user_id, "role": "super_admin"},
        ticket_id=str(tid),
    )
    assert resp["comments"][0]["_id"] == str(comments.documents[0]["_id"])

    # get_tickets status/priority lower branches
    bcode = "BB"
    uid = ObjectId()
    list_board_id = boards.insert_one({"_id": ObjectId(), "code": bcode}).inserted_id
    grant_board_role(list_board_id, uid, "member")
    tickets.insert_one(
        {
            "_id": ObjectId(),
            "board_code": bcode,
            "createdAt": datetime.now(timezone.utc),
            "priorityOrder": 1,
            "dueDate": datetime.now(timezone.utc),
            "userId": str(uid),
            "title": "T",
            "ticketNumber": "TN",
            "status": "open",
            "priority": "high",
        }
    )
    res = ticket_mod.get_tickets(
        bcode,
        page=1,
        page_size=10,
        status="open",
        priority="high",
        category=None,
        assigned_agent=None,
        search=None,
        sort_by="created_at",
        sort_order="desc",
        current_user={"_id": uid, "role": "agent"},
    )
    assert "data" in res

    # move to last status sets closed_date
    s1 = ObjectId()
    s2 = ObjectId()
    s3 = ObjectId()
    b = boards.insert_one(
        {
            "_id": ObjectId(),
            "assigned_statuses": [str(s1), str(s2), str(s3)],
            "code": "BB",
        }
    ).inserted_id
    grant_board_role(b, uid, "member")
    statuses.insert_one({"_id": s3, "name": "Last"})
    tt = tickets.insert_one(
        {
            "_id": ObjectId(),
            "board_id": str(b),
            "userId": str(uid),
            "ticket_code": "TX",
            "current_status_id": str(s2),
            "current_status_name": "Middle",
        }
    ).inserted_id
    resp2 = ticket_mod.move_ticket_forward(
        str(tt), current_user={"_id": uid, "role": "super_admin"}
    )
    assert resp2["current_status"] == "Last"
    # ensure closed_date updated in stored ticket
    updated = tickets.find_one({"_id": tt})
    assert "closed_date" in updated


def test_auth_get_current_user_no_sub_and_success(monkeypatch, fake_collection):
    fake_users = fake_collection.__class__()
    monkeypatch.setattr(auth, "user_collection", fake_users)
    from fastapi.security import HTTPAuthorizationCredentials

    # no sub
    monkeypatch.setattr(auth.jwt, "decode", lambda token, s, algorithms: {})
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="tok")
    with pytest.raises(HTTPException) as exc:
        auth.get_current_user(token=creds)
    assert exc.value.status_code == 401

    # success
    uid = ObjectId()
    fake_users.insert_one({"_id": uid, "name": "U"})
    monkeypatch.setattr(
        auth.jwt, "decode", lambda token, s, algorithms: {"sub": str(uid)}
    )
    creds2 = HTTPAuthorizationCredentials(scheme="Bearer", credentials="tok2")
    user = auth.get_current_user(token=creds2)
    assert user["name"] == "U"


def test_activity_log_inserts(monkeypatch, fake_collection):
    acts = fake_collection.__class__()
    monkeypatch.setattr(activity, "ticket_activity_collection", acts)
    activity.log_activity(
        ticket_id="t1", action="a", changed_by="u", changes=[{"f": 1}]
    )
    assert len(acts.inserted_documents) == 1


def test_status_permissions_and_delete_missing(monkeypatch, fake_collection):
    statuses = fake_collection.__class__()
    boards = fake_collection.__class__()
    monkeypatch.setattr(status_mod, "statuses_collection", statuses)
    monkeypatch.setattr(status_mod, "boards_collection", boards)

    # update by non-admin
    with pytest.raises(HTTPException):
        status_mod.update_status(
            str(ObjectId()), StatusUpdate(name="X"), current_user={"role": "agent"}
        )

    # delete by non-admin
    with pytest.raises(HTTPException):
        status_mod.delete_status(str(ObjectId()), current_user={"role": "agent"})

    # delete missing
    with pytest.raises(HTTPException):
        status_mod.delete_status(str(ObjectId()), current_user={"role": "super_admin"})


def test_board_assign_not_found_and_reassign_forbidden(monkeypatch, fake_collection):
    boards = fake_collection.__class__()
    monkeypatch.setattr(board_mod, "boards_collection", boards)

    import asyncio

    # assign to non-existing board -> 404
    with pytest.raises(HTTPException):
        asyncio.run(
            board_mod.assign_board(
                str(ObjectId()),
                AssignBoard(agent_id="a"),
                current_user={"_id": ObjectId(), "role": "super_admin"},
            )
        )

    # create a board assigned to someone else
    bid = boards.insert_one({"_id": ObjectId(), "assigned_agent": "other"}).inserted_id
    # non-super_admin trying to reassign should 403
    with pytest.raises(HTTPException):
        asyncio.run(
            board_mod.assign_board(
                str(bid),
                AssignBoard(agent_id="new"),
                current_user={"_id": ObjectId(), "role": "agent"},
            )
        )


def test_assign_statuses_board_not_found_and_status_not_found(
    monkeypatch, fake_collection, grant_board_role
):
    boards = fake_collection.__class__()
    statuses = fake_collection.__class__()
    monkeypatch.setattr(board_mod, "boards_collection", boards)
    monkeypatch.setattr(board_mod, "statuses_collection", statuses)

    from app.models.board import BoardStatusAssign

    # board not found
    with pytest.raises(HTTPException):
        board_mod.assign_statuses_to_board(
            str(ObjectId()),
            BoardStatusAssign(status_ids=[str(ObjectId()), str(ObjectId())]),
            current_user={"_id": ObjectId(), "role": "super_admin"},
        )

    # create board and one missing status -> should 404 when checking statuses
    b = boards.insert_one({"_id": ObjectId()}).inserted_id
    lead_id = ObjectId()
    grant_board_role(b, lead_id)
    with pytest.raises(HTTPException):
        board_mod.assign_statuses_to_board(
            str(b),
            BoardStatusAssign(status_ids=[str(ObjectId()), str(ObjectId())]),
            current_user={"_id": lead_id, "role": "super_admin"},
        )


def test_assign_board_inner_reassign_forbidden(
    monkeypatch, fake_collection, grant_board_role
):
    boards = fake_collection.__class__()
    monkeypatch.setattr(board_mod, "boards_collection", boards)
    bid = boards.insert_one({"_id": ObjectId(), "assigned_agent": "a1"}).inserted_id
    member_id = ObjectId()
    grant_board_role(bid, member_id, "member")

    with pytest.raises(HTTPException):
        board_mod.assign_board(
            str(bid),
            AssignBoard(agent_id=str(ObjectId())),
            current_user={"_id": member_id, "role": "agent"},
        )


def test_ticket_lower_filters_and_move_forbidden(
    monkeypatch, fake_collection, grant_board_role
):
    tickets = fake_collection.__class__()
    boards = fake_collection.__class__()
    statuses = fake_collection.__class__()
    monkeypatch.setattr(ticket_mod, "ticket_collection", tickets)
    monkeypatch.setattr(ticket_mod, "boards_collection", boards)
    monkeypatch.setattr(ticket_mod, "statuses_collection", statuses)

    bcode = "ZC"
    board_id = boards.insert_one({"_id": ObjectId(), "code": bcode}).inserted_id
    uid = ObjectId()
    grant_board_role(board_id, uid, "reporter")
    # add ticket belonging to someone else
    tickets.insert_one(
        {
            "_id": ObjectId(),
            "board_code": bcode,
            "createdAt": datetime.now(timezone.utc),
            "priorityOrder": 1,
            "dueDate": datetime.now(timezone.utc),
            "userId": str(ObjectId()),
            "title": "X",
            "ticketNumber": "TN",
            "status": "OPEN",
            "priority": "HIGH",
        }
    )

    # call get_tickets with uppercase status/priority to exercise .lower()
    res = ticket_mod.get_tickets(
        bcode,
        page=1,
        page_size=10,
        status="OPEN",
        priority="HIGH",
        category=None,
        assigned_agent=None,
        search=None,
        sort_by="created_at",
        sort_order="desc",
        current_user={"_id": uid, "role": "super_admin"},
    )
    assert "data" in res

    # move_ticket_forward forbidden for agent who is not owner
    move_board_id = ObjectId()
    t = tickets.insert_one(
        {
            "_id": ObjectId(),
            "board_id": str(move_board_id),
            "userId": str(ObjectId()),
            "ticket_code": "TF",
            "current_status_id": str(ObjectId()),
            "current_status_name": "S",
        }
    ).inserted_id
    grant_board_role(move_board_id, uid, "reporter")
    with pytest.raises(HTTPException):
        ticket_mod.move_ticket_forward(
            str(t), current_user={"_id": uid, "role": "agent"}
        )
