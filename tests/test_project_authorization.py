from datetime import datetime, timezone

import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.api import board, comment, ticket
from app.models.board import BoardStatusAssign
from app.models.comment import Comment
from app.models.ticket import AssignAgent, TicketStatusMove


def add_membership(board_members, board_id, user_id, role):
    board_members.insert_one(
        {
            "board_id": board_id,
            "user_id": user_id,
            "role": role,
        }
    )


def test_global_super_admin_cannot_view_board_without_membership(
    monkeypatch, fake_collection
):
    board_id = ObjectId()
    monkeypatch.setattr(
        board,
        "boards_collection",
        fake_collection.__class__([{"_id": board_id, "code": "SEC"}]),
    )

    with pytest.raises(HTTPException) as exc_info:
        board.get_board(
            str(board_id),
            current_user={"_id": ObjectId(), "role": "super_admin"},
        )

    assert exc_info.value.status_code == 403


def test_ticket_assignment_does_not_grant_board_access(monkeypatch, fake_collection):
    board_id = ObjectId()
    ticket_id = ObjectId()
    assigned_user_id = ObjectId()
    monkeypatch.setattr(
        ticket,
        "ticket_collection",
        fake_collection.__class__(
            [
                {
                    "_id": ticket_id,
                    "board_id": str(board_id),
                    "assigned_agent": str(assigned_user_id),
                }
            ]
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        ticket.get_ticket_details(
            ticket_id=str(ticket_id),
            current_user={"_id": assigned_user_id, "role": "agent"},
        )

    assert exc_info.value.status_code == 403


@pytest.mark.parametrize("role", ["lead", "member", "reporter"])
def test_each_project_role_can_view_board(
    role, monkeypatch, fake_collection, board_members
):
    board_id = ObjectId()
    user_id = ObjectId()
    monkeypatch.setattr(
        board,
        "boards_collection",
        fake_collection.__class__([{"_id": board_id, "code": "VIEW"}]),
    )
    add_membership(board_members, board_id, user_id, role)

    response = board.get_board(
        str(board_id),
        current_user={"_id": user_id, "role": "customer"},
    )

    assert response["board"]["code"] == "VIEW"


def test_board_list_only_contains_memberships(
    monkeypatch, fake_collection, board_members
):
    user_id = ObjectId()
    visible_board_id = ObjectId()
    hidden_board_id = ObjectId()
    boards = fake_collection.__class__(
        [
            {"_id": visible_board_id, "code": "YES"},
            {"_id": hidden_board_id, "code": "NO"},
        ]
    )
    monkeypatch.setattr(board, "boards_collection", boards)
    monkeypatch.setattr(board, "ticket_collection", fake_collection.__class__())
    add_membership(board_members, visible_board_id, user_id, "reporter")

    response = board.get_boards(current_user={"_id": user_id, "role": "admin"})

    assert [item["code"] for item in response["boards"]] == ["YES"]


def test_only_board_lead_can_assign_workflow_statuses(
    monkeypatch, fake_collection, board_members
):
    board_id = ObjectId()
    lead_id = ObjectId()
    member_id = ObjectId()
    first_status_id = ObjectId()
    second_status_id = ObjectId()
    boards = fake_collection.__class__([{"_id": board_id}])
    statuses = fake_collection.__class__(
        [
            {"_id": first_status_id, "name": "Open", "order": 1},
            {"_id": second_status_id, "name": "Done", "order": 2},
        ]
    )
    monkeypatch.setattr(board, "boards_collection", boards)
    monkeypatch.setattr(board, "statuses_collection", statuses)
    add_membership(board_members, board_id, lead_id, "lead")
    add_membership(board_members, board_id, member_id, "member")
    payload = BoardStatusAssign(
        status_ids=[str(second_status_id), str(first_status_id)]
    )

    response = board.assign_statuses_to_board(
        str(board_id),
        payload,
        current_user={"_id": lead_id, "role": "user"},
    )
    assert response["first_status"]["name"] == "Open"

    with pytest.raises(HTTPException) as exc_info:
        board.assign_statuses_to_board(
            str(board_id),
            payload,
            current_user={"_id": member_id, "role": "super_admin"},
        )

    assert exc_info.value.status_code == 403


def test_board_lead_can_assign_ticket_to_project_member_regardless_of_global_role(
    monkeypatch, fake_collection, board_members
):
    board_id = ObjectId()
    ticket_id = ObjectId()
    lead_id = ObjectId()
    assignee_id = ObjectId()
    tickets = fake_collection.__class__(
        [
            {
                "_id": ticket_id,
                "board_id": str(board_id),
                "assigned_agent": None,
            }
        ]
    )
    users = fake_collection.__class__(
        [{"_id": assignee_id, "role": "customer", "is_active": True}]
    )
    notifications = fake_collection.__class__()
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "user_collection", users)
    monkeypatch.setattr(ticket, "notifications_collection", notifications)
    add_membership(board_members, board_id, lead_id, "lead")
    add_membership(board_members, board_id, assignee_id, "member")

    response = ticket.assign_ticket(
        str(ticket_id),
        AssignAgent(agent_id=str(assignee_id)),
        current_user={"_id": lead_id, "role": "customer"},
    )

    assert response["message"] == "Ticket assigned successfully"
    assert tickets.documents[0]["assigned_agent"] == str(assignee_id)


def test_project_member_can_self_assign_but_cannot_reassign_another_member(
    monkeypatch, fake_collection, board_members
):
    board_id = ObjectId()
    ticket_id = ObjectId()
    member_id = ObjectId()
    other_member_id = ObjectId()
    tickets = fake_collection.__class__(
        [
            {
                "_id": ticket_id,
                "board_id": str(board_id),
                "assigned_agent": None,
            }
        ]
    )
    users = fake_collection.__class__(
        [
            {"_id": member_id, "role": "user"},
            {"_id": other_member_id, "role": "agent"},
        ]
    )
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "user_collection", users)
    monkeypatch.setattr(ticket, "notifications_collection", fake_collection.__class__())
    add_membership(board_members, board_id, member_id, "member")
    add_membership(board_members, board_id, other_member_id, "member")

    ticket.assign_ticket(
        str(ticket_id),
        AssignAgent(agent_id=str(member_id)),
        current_user={"_id": member_id, "role": "user"},
    )

    with pytest.raises(HTTPException) as exc_info:
        ticket.assign_ticket(
            str(ticket_id),
            AssignAgent(agent_id=str(other_member_id)),
            current_user={"_id": member_id, "role": "admin"},
        )

    assert exc_info.value.status_code == 403
    assert tickets.documents[0]["assigned_agent"] == str(member_id)


def test_board_member_can_view_all_board_tickets(
    monkeypatch, fake_collection, board_members
):
    board_id = ObjectId()
    user_id = ObjectId()
    other_user_id = ObjectId()
    boards = fake_collection.__class__([{"_id": board_id, "code": "ALL"}])
    tickets = fake_collection.__class__(
        [
            {
                "_id": ObjectId(),
                "board_id": str(board_id),
                "board_code": "ALL",
                "ticket_code": "ALL-1",
                "userId": str(other_user_id),
                "created_date": datetime.now(timezone.utc),
            }
        ]
    )
    monkeypatch.setattr(ticket, "boards_collection", boards)
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    add_membership(board_members, board_id, user_id, "reporter")

    response = ticket.get_tickets(
        "ALL",
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
        current_user={"_id": user_id, "role": "customer"},
    )

    assert [item["ticket_code"] for item in response["data"]] == ["ALL-1"]


def test_reporter_cannot_change_ticket_status(
    monkeypatch, fake_collection, board_members
):
    board_id = ObjectId()
    ticket_id = ObjectId()
    reporter_id = ObjectId()
    status_id = ObjectId()
    monkeypatch.setattr(
        ticket,
        "ticket_collection",
        fake_collection.__class__([{"_id": ticket_id, "board_id": str(board_id)}]),
    )
    add_membership(board_members, board_id, reporter_id, "reporter")

    with pytest.raises(HTTPException) as exc_info:
        ticket.move_ticket_status(
            str(ticket_id),
            TicketStatusMove(target_status_id=str(status_id)),
            current_user={"_id": reporter_id, "role": "admin"},
        )

    assert exc_info.value.status_code == 403


def test_reporter_only_sees_public_comments(
    monkeypatch, fake_collection, board_members
):
    board_id = ObjectId()
    ticket_id = ObjectId()
    reporter_id = ObjectId()
    monkeypatch.setattr(
        comment,
        "ticket_collection",
        fake_collection.__class__([{"_id": ticket_id, "board_id": str(board_id)}]),
    )
    monkeypatch.setattr(
        comment,
        "comments_collection",
        fake_collection.__class__(
            [
                {
                    "_id": ObjectId(),
                    "ticket_id": str(ticket_id),
                    "visibility": "public",
                    "message": "visible",
                },
                {
                    "_id": ObjectId(),
                    "ticket_id": str(ticket_id),
                    "visibility": "internal",
                    "message": "hidden",
                },
            ]
        ),
    )
    add_membership(board_members, board_id, reporter_id, "reporter")

    response = comment.get_comments(
        str(ticket_id),
        current_user={"_id": reporter_id, "role": "super_admin"},
    )

    assert [item["message"] for item in response] == ["visible"]

    with pytest.raises(HTTPException) as exc_info:
        comment.create_comment(
            str(ticket_id),
            Comment(message="private", visibility="internal"),
            current_user={"_id": reporter_id, "role": "super_admin"},
        )

    assert exc_info.value.status_code == 403
