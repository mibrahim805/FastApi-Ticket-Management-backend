import pytest
from bson import ObjectId
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from app.api import ticket
from app.main import app
from app.models.ticket import IssueLinkCreate, IssueLinkType


@pytest.mark.parametrize("link_type", list(IssueLinkType))
def test_create_ticket_link_persists_supported_link_types(
    monkeypatch,
    fake_collection,
    issue_links,
    ticket_activities,
    grant_project_role,
    link_type,
):
    project_id = ObjectId()
    from_ticket_id = ObjectId()
    to_ticket_id = ObjectId()
    user_id = ObjectId()
    tickets = fake_collection.__class__(
        [
            {"_id": from_ticket_id, "project_id": str(project_id)},
            {"_id": to_ticket_id, "project_id": str(project_id)},
        ]
    )
    monkeypatch.setattr(ticket, "ticket_collection", tickets)
    monkeypatch.setattr(ticket, "issue_links_collection", issue_links)
    grant_project_role(project_id, user_id, "member")

    response = ticket.create_ticket_link(
        str(from_ticket_id),
        IssueLinkCreate(
            to_ticket_id=str(to_ticket_id),
            link_type=link_type,
        ),
        current_user={"_id": user_id, "role": "agent"},
    )

    assert response == {
        "_id": str(issue_links.documents[0]["_id"]),
        "from_ticket_id": str(from_ticket_id),
        "to_ticket_id": str(to_ticket_id),
        "link_type": link_type.value,
    }
    assert [activity["action"] for activity in ticket_activities.documents] == [
        "issue_link_created",
        "issue_link_created",
    ]


def test_create_ticket_link_rejects_duplicate(
    monkeypatch, fake_collection, issue_links, grant_project_role
):
    project_id = ObjectId()
    from_ticket_id = ObjectId()
    to_ticket_id = ObjectId()
    user_id = ObjectId()
    monkeypatch.setattr(
        ticket,
        "ticket_collection",
        fake_collection.__class__(
            [
                {"_id": from_ticket_id, "project_id": str(project_id)},
                {"_id": to_ticket_id, "project_id": str(project_id)},
            ]
        ),
    )
    monkeypatch.setattr(ticket, "issue_links_collection", issue_links)
    grant_project_role(project_id, user_id, "member")
    payload = IssueLinkCreate(
        to_ticket_id=str(to_ticket_id),
        link_type=IssueLinkType.BLOCKS,
    )

    ticket.create_ticket_link(
        str(from_ticket_id),
        payload,
        current_user={"_id": user_id, "role": "agent"},
    )
    with pytest.raises(HTTPException) as exc_info:
        ticket.create_ticket_link(
            str(from_ticket_id),
            payload,
            current_user={"_id": user_id, "role": "agent"},
        )

    assert exc_info.value.status_code == 409
    assert len(issue_links.documents) == 1


def test_create_ticket_link_handles_unique_index_race(
    monkeypatch, fake_collection, grant_project_role
):
    project_id = ObjectId()
    from_ticket_id = ObjectId()
    to_ticket_id = ObjectId()
    user_id = ObjectId()

    class DuplicateOnInsertCollection(fake_collection.__class__):
        def insert_one(self, document):
            raise DuplicateKeyError("duplicate issue link")

    monkeypatch.setattr(
        ticket,
        "ticket_collection",
        fake_collection.__class__(
            [
                {"_id": from_ticket_id, "project_id": str(project_id)},
                {"_id": to_ticket_id, "project_id": str(project_id)},
            ]
        ),
    )
    monkeypatch.setattr(
        ticket,
        "issue_links_collection",
        DuplicateOnInsertCollection(),
    )
    grant_project_role(project_id, user_id, "member")

    with pytest.raises(HTTPException) as exc_info:
        ticket.create_ticket_link(
            str(from_ticket_id),
            IssueLinkCreate(
                to_ticket_id=str(to_ticket_id),
                link_type=IssueLinkType.RELATES_TO,
            ),
            current_user={"_id": user_id, "role": "agent"},
        )

    assert exc_info.value.status_code == 409


def test_create_ticket_link_rejects_self_link(
    monkeypatch, fake_collection, grant_project_role
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    user_id = ObjectId()
    monkeypatch.setattr(
        ticket,
        "ticket_collection",
        fake_collection.__class__([{"_id": ticket_id, "project_id": str(project_id)}]),
    )
    grant_project_role(project_id, user_id, "member")

    with pytest.raises(HTTPException) as exc_info:
        ticket.create_ticket_link(
            str(ticket_id),
            IssueLinkCreate(
                to_ticket_id=str(ticket_id),
                link_type=IssueLinkType.DUPLICATES,
            ),
            current_user={"_id": user_id, "role": "agent"},
        )

    assert exc_info.value.status_code == 400


def test_create_cross_project_link_requires_target_membership(
    monkeypatch, fake_collection, grant_project_role
):
    from_project_id = ObjectId()
    to_project_id = ObjectId()
    from_ticket_id = ObjectId()
    to_ticket_id = ObjectId()
    user_id = ObjectId()
    monkeypatch.setattr(
        ticket,
        "ticket_collection",
        fake_collection.__class__(
            [
                {"_id": from_ticket_id, "project_id": str(from_project_id)},
                {"_id": to_ticket_id, "project_id": str(to_project_id)},
            ]
        ),
    )
    grant_project_role(from_project_id, user_id, "member")

    with pytest.raises(HTTPException) as exc_info:
        ticket.create_ticket_link(
            str(from_ticket_id),
            IssueLinkCreate(
                to_ticket_id=str(to_ticket_id),
                link_type=IssueLinkType.RELATES_TO,
            ),
            current_user={"_id": user_id, "role": "agent"},
        )

    assert exc_info.value.status_code == 403


def test_get_ticket_links_returns_incoming_and_outgoing_links(
    monkeypatch, fake_collection, issue_links, grant_project_role
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    first_linked_ticket_id = ObjectId()
    second_linked_ticket_id = ObjectId()
    unrelated_ticket_id = ObjectId()
    user_id = ObjectId()
    monkeypatch.setattr(
        ticket,
        "ticket_collection",
        fake_collection.__class__([{"_id": ticket_id, "project_id": str(project_id)}]),
    )
    issue_links.documents.extend(
        [
            {
                "_id": ObjectId(),
                "from_ticket_id": str(ticket_id),
                "to_ticket_id": str(first_linked_ticket_id),
                "link_type": "blocks",
            },
            {
                "_id": ObjectId(),
                "from_ticket_id": str(second_linked_ticket_id),
                "to_ticket_id": str(ticket_id),
                "link_type": "duplicates",
            },
            {
                "_id": ObjectId(),
                "from_ticket_id": str(first_linked_ticket_id),
                "to_ticket_id": str(unrelated_ticket_id),
                "link_type": "relates_to",
            },
        ]
    )
    monkeypatch.setattr(ticket, "issue_links_collection", issue_links)
    grant_project_role(project_id, user_id, "reporter")

    response = ticket.get_ticket_links(
        str(ticket_id),
        current_user={"_id": user_id, "role": "agent"},
    )

    assert len(response["links"]) == 2
    assert {link["link_type"] for link in response["links"]} == {
        "blocks",
        "duplicates",
    }
    assert all(isinstance(link["_id"], str) for link in response["links"])


def test_delete_ticket_archives_ticket_and_preserves_associated_issue_links(
    monkeypatch,
    fake_collection,
    issue_links,
    ticket_activities,
    grant_project_role,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    other_ticket_id = ObjectId()
    third_ticket_id = ObjectId()
    user_id = ObjectId()
    monkeypatch.setattr(
        ticket,
        "ticket_collection",
        fake_collection.__class__([{"_id": ticket_id, "project_id": str(project_id)}]),
    )
    monkeypatch.setattr(
        ticket,
        "notifications_collection",
        fake_collection.__class__(),
    )
    issue_links.documents.extend(
        [
            {
                "_id": ObjectId(),
                "from_ticket_id": str(ticket_id),
                "to_ticket_id": str(other_ticket_id),
                "link_type": "blocks",
            },
            {
                "_id": ObjectId(),
                "from_ticket_id": str(other_ticket_id),
                "to_ticket_id": str(ticket_id),
                "link_type": "duplicates",
            },
            {
                "_id": ObjectId(),
                "from_ticket_id": str(other_ticket_id),
                "to_ticket_id": str(third_ticket_id),
                "link_type": "relates_to",
            },
        ]
    )
    monkeypatch.setattr(ticket, "issue_links_collection", issue_links)
    grant_project_role(project_id, user_id, "lead")

    ticket.delete_ticket(
        str(ticket_id),
        current_user={"_id": user_id, "role": "agent"},
    )

    assert len(issue_links.documents) == 3
    archived_ticket = ticket.ticket_collection.documents[0]
    assert archived_ticket["deleted_by"] == str(user_id)
    assert archived_ticket["deletion_reason"] == "deleted_by_user"
    assert ticket_activities.documents[-1]["action"] == "ticket_deleted"


def test_issue_link_routes_and_enum_are_exposed_in_openapi():
    openapi = app.openapi()
    operations = openapi["paths"]["/tickets/{ticket_id}/links"]

    assert {"post", "get"}.issubset(operations)
    assert openapi["components"]["schemas"]["IssueLinkType"]["enum"] == [
        "blocks",
        "relates_to",
        "duplicates",
    ]
