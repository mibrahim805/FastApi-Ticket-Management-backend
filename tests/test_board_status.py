import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.api import board, status
from app.models.board import BoardStatusAssign
from app.models.status import StatusCreate, StatusUpdate


def test_assign_statuses_to_board_orders_statuses(monkeypatch, fake_collection):
    board_id = ObjectId()
    first_id = ObjectId()
    second_id = ObjectId()

    boards = fake_collection.__class__([{"_id": board_id}])
    statuses = fake_collection.__class__(
        [
            {"_id": second_id, "name": "Done", "order": 2},
            {"_id": first_id, "name": "Open", "order": 1},
        ]
    )

    monkeypatch.setattr(board, "boards_collection", boards)
    monkeypatch.setattr(board, "statuses_collection", statuses)

    response = board.assign_statuses_to_board(
        str(board_id),
        BoardStatusAssign(status_ids=[str(second_id), str(first_id)]),
        current_user={"_id": ObjectId(), "role": "super_admin"},
    )

    assert response["assigned_statuses"][0]["name"] == "Open"
    assert boards.documents[0]["assigned_statuses"] == [str(first_id), str(second_id)]


def test_assign_statuses_to_board_rejects_non_admin(monkeypatch, fake_collection):
    board_id = ObjectId()
    boards = fake_collection.__class__([{"_id": board_id}])
    monkeypatch.setattr(board, "boards_collection", boards)
    monkeypatch.setattr(board, "statuses_collection", fake_collection.__class__())

    with pytest.raises(HTTPException) as exc_info:
        board.assign_statuses_to_board(
            str(board_id),
            BoardStatusAssign(status_ids=[str(ObjectId()), str(ObjectId())]),
            current_user={"_id": ObjectId(), "role": "agent"},
        )

    assert exc_info.value.status_code == 403


def test_assign_statuses_to_board_rejects_duplicate_ids(monkeypatch, fake_collection):
    board_id = ObjectId()
    status_id = ObjectId()
    boards = fake_collection.__class__([{"_id": board_id}])
    monkeypatch.setattr(board, "boards_collection", boards)
    monkeypatch.setattr(
        board,
        "statuses_collection",
        fake_collection.__class__([{"_id": status_id, "name": "Open", "order": 1}]),
    )

    with pytest.raises(HTTPException) as exc_info:
        board.assign_statuses_to_board(
            str(board_id),
            BoardStatusAssign(status_ids=[str(status_id), str(status_id)]),
            current_user={"_id": ObjectId(), "role": "super_admin"},
        )

    assert exc_info.value.status_code == 400


def test_create_status_rejects_duplicate_name(monkeypatch, fake_collection):
    statuses = fake_collection.__class__([{"name": "Open"}])
    monkeypatch.setattr(status, "statuses_collection", statuses)

    with pytest.raises(HTTPException) as exc_info:
        status.create_status(
            StatusCreate(name="Open", order=1),
            current_user={"_id": ObjectId(), "role": "super_admin"},
        )

    assert exc_info.value.status_code == 400


def test_create_status_rejects_non_admin(monkeypatch, fake_collection):
    monkeypatch.setattr(status, "statuses_collection", fake_collection)

    with pytest.raises(HTTPException) as exc_info:
        status.create_status(
            StatusCreate(name="Open", order=1),
            current_user={"_id": ObjectId(), "role": "agent"},
        )

    assert exc_info.value.status_code == 403


def test_update_status_rejects_empty_payload(monkeypatch, fake_collection):
    status_id = ObjectId()
    statuses = fake_collection.__class__([{"_id": status_id, "name": "Open"}])
    monkeypatch.setattr(status, "statuses_collection", statuses)

    with pytest.raises(HTTPException) as exc_info:
        status.update_status(
            str(status_id),
            StatusUpdate(),
            current_user={"_id": ObjectId(), "role": "super_admin"},
        )

    assert exc_info.value.status_code == 400


def test_update_status_rejects_missing_status(monkeypatch, fake_collection):
    monkeypatch.setattr(status, "statuses_collection", fake_collection)

    with pytest.raises(HTTPException) as exc_info:
        status.update_status(
            str(ObjectId()),
            StatusUpdate(name="New"),
            current_user={"_id": ObjectId(), "role": "super_admin"},
        )

    assert exc_info.value.status_code == 404
