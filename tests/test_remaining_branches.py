from datetime import datetime, timezone

import pytest
from bson import ObjectId
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import JWTError

from app.api import activity, auth
from app.api import project as project_mod
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


def test_activity_get_ticket_activity_success(
    monkeypatch, fake_collection, grant_project_role
):
    ticket_id = str(ObjectId())
    project_id = ObjectId()
    user_id = ObjectId()
    t = {
        "_id": ObjectId(ticket_id),
        "project_id": str(project_id),
        "ticket_code": "T-1",
    }
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
    grant_project_role(project_id, user_id, "reporter")

    resp = activity.get_ticket_activity(
        ticket_id, current_user={"_id": user_id, "role": "customer"}
    )
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
    projects = fake_collection.__class__()
    monkeypatch.setattr(status_mod, "statuses_collection", statuses)
    monkeypatch.setattr(status_mod, "projects_collection", projects)

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

    # delete: assigned to project
    projects.insert_one({"assigned_statuses": str(sid)}).inserted_id
    with pytest.raises(HTTPException):
        status_mod.delete_status(str(sid), current_user={"role": "super_admin"})

    # remove assignment and delete
    projects.documents = []
    resp3 = status_mod.delete_status(str(sid), current_user={"role": "super_admin"})
    assert resp3["message"] == "Status deleted successfully"


def test_project_get_update_delete(monkeypatch, fake_collection, grant_project_role):
    projects = fake_collection.__class__()
    tickets = fake_collection.__class__()
    monkeypatch.setattr(project_mod, "projects_collection", projects)
    monkeypatch.setattr(project_mod, "ticket_collection", tickets)

    # create
    from app.models.project import ProjectCreate

    super_admin_id = ObjectId()
    resp = project_mod.create_project(
        ProjectCreate(name="B", code="BC"),
        current_user={"_id": super_admin_id, "role": "super_admin"},
    )
    assert "id" in resp

    # get_projects
    projects.documents.append({"_id": ObjectId(), "code": "C1"})
    gb = project_mod.get_projects(
        current_user={"_id": super_admin_id, "role": "super_admin"}
    )
    assert "projects" in gb

    # get_project not found
    with pytest.raises(HTTPException):
        project_mod.get_project(
            str(ObjectId()), current_user={"_id": ObjectId(), "role": "super_admin"}
        )

    # insert and unauthorized view (assigned_agent mismatch)
    different_agent_id = ObjectId()
    projects.insert_one(
        {"_id": ObjectId(), "assigned_agent": str(different_agent_id)}
    ).inserted_id
    with pytest.raises(HTTPException) as exc_info:
        project_mod.get_project(
            str(projects.documents[-1]["_id"]),
            current_user={"_id": ObjectId(), "role": "agent"},
        )
    assert exc_info.value.status_code == 403

    # update
    with pytest.raises(HTTPException):
        project_mod.update_project(
            {"name": "X"},
            str(ObjectId()),
            current_user={"_id": super_admin_id, "role": "super_admin"},
        )

    # delete not found
    with pytest.raises(HTTPException):
        project_mod.delete_project(
            str(ObjectId()),
            current_user={"_id": super_admin_id, "role": "super_admin"},
        )


def test_ticket_get_update_summary_delete(
    monkeypatch, fake_collection, grant_project_role
):
    tickets = fake_collection.__class__()
    comments = fake_collection.__class__()
    notifications = fake_collection.__class__()
    projects = fake_collection.__class__()
    statuses = fake_collection.__class__()
    users = fake_collection.__class__()

    monkeypatch.setattr(ticket_mod, "ticket_collection", tickets)
    monkeypatch.setattr(ticket_mod, "comments_collection", comments)
    monkeypatch.setattr(ticket_mod, "notifications_collection", notifications)
    monkeypatch.setattr(ticket_mod, "projects_collection", projects)
    monkeypatch.setattr(ticket_mod, "statuses_collection", statuses)
    monkeypatch.setattr(ticket_mod, "user_collection", users)
    monkeypatch.setattr(ticket_mod, "log_activity", lambda **k: None)

    # get details missing
    with pytest.raises(HTTPException):
        ticket_mod.get_ticket_details(
            current_user={"_id": ObjectId(), "role": "agent"}, ticket_id=str(ObjectId())
        )

    # insert ticket and comments and get details unauthorized
    detail_project_id = ObjectId()
    tid = tickets.insert_one(
        {
            "_id": ObjectId(),
            "project_id": str(detail_project_id),
            "userId": str(ObjectId()),
        }
    ).inserted_id
    with pytest.raises(HTTPException):
        ticket_mod.get_ticket_details(
            current_user={"_id": ObjectId(), "role": "agent"}, ticket_id=str(tid)
        )

    # now as super_admin
    detail_admin_id = ObjectId()
    grant_project_role(detail_project_id, detail_admin_id, "reporter")
    resp = ticket_mod.get_ticket_details(
        current_user={"_id": detail_admin_id, "role": "super_admin"},
        ticket_id=str(tid),
    )
    assert "tickets" in resp

    # update ticket
    # create ticket with fields
    t_owner = ObjectId()
    update_project_id = ObjectId()
    tid2 = tickets.insert_one(
        {
            "_id": ObjectId(),
            "project_id": str(update_project_id),
            "userId": str(t_owner),
            "title": "T",
            "description": "D",
            "priority": "low",
            "status": "open",
            "category": "support",
            "assigned_agent": str(ObjectId()),
        }
    ).inserted_id
    upd = TicketUpdate(priority="high", category="bug")
    grant_project_role(update_project_id, t_owner, "member")
    resp2 = ticket_mod.update_ticket(
        str(tid2), upd, current_user={"_id": t_owner, "role": "agent"}
    )
    assert resp2["message"] == "Ticket updated successfully"

    # get_tickets with project not found
    with pytest.raises(HTTPException):
        ticket_mod.get_tickets(
            "NOPROJECT", current_user={"_id": ObjectId(), "role": "agent"}
        )

    # create project and tickets and test pagination and filters
    project_key = "BC"
    list_project_id = projects.insert_one(
        {"_id": ObjectId(), "code": project_key}
    ).inserted_id
    list_user_id = ObjectId()
    grant_project_role(list_project_id, list_user_id, "reporter")
    tickets.insert_one(
        {
            "_id": ObjectId(),
            "project_id": str(list_project_id),
            "project_key": project_key,
            "createdAt": datetime.now(),
            "priorityOrder": 1,
            "dueDate": datetime.now(),
            "userId": str(ObjectId()),
        }
    )
    res = ticket_mod.get_tickets(
        project_key,
        page=1,
        page_size=1,
        status=None,
        priority=None,
        category=None,
        assigned_agent=None,
        search=None,
        sort_by="created_at",
        sort_order="desc",
        current_user={"_id": list_user_id, "role": "super_admin"},
    )
    assert "data" in res

    # summary empty
    summ = ticket_mod.get_tickets_summary(
        current_user={"_id": list_user_id, "role": "super_admin"}
    )
    assert isinstance(summ, dict)

    # delete ticket not found
    with pytest.raises(HTTPException):
        ticket_mod.delete_ticket(
            str(ObjectId()), current_user={"_id": ObjectId(), "role": "agent"}
        )

    # insert and delete success
    delete_project_id = ObjectId()
    delete_lead_id = ObjectId()
    td = tickets.insert_one(
        {
            "_id": ObjectId(),
            "project_id": str(delete_project_id),
            "userId": str(ObjectId()),
        }
    ).inserted_id
    grant_project_role(delete_project_id, delete_lead_id)
    resp3 = ticket_mod.delete_ticket(
        str(td), current_user={"_id": delete_lead_id, "role": "super_admin"}
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
