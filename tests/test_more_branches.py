from datetime import datetime, timezone

import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.api import activity, auth
from app.api import project as project_mod
from app.api import status as status_mod
from app.api import ticket as ticket_mod
from app.models.status import StatusUpdate
from app.models.ticket import Category, Priority, TicketUpdate


def test_activity_log_and_conversion(monkeypatch, fake_collection, grant_project_role):
    # ensure log_activity inserts and get_ticket_activity converts ObjectId and datetime
    acts = fake_collection.__class__()
    project_id = ObjectId()
    user_id = ObjectId()
    tickets = fake_collection.__class__(
        [{"_id": ObjectId(), "project_id": str(project_id), "ticket_code": "X-1"}]
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
    grant_project_role(project_id, user_id, "member")
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


def test_project_update_get_delete_success(
    monkeypatch, fake_collection, grant_project_role
):
    projects = fake_collection.__class__()
    tickets = fake_collection.__class__()
    monkeypatch.setattr(project_mod, "projects_collection", projects)
    monkeypatch.setattr(project_mod, "ticket_collection", tickets)

    lead_id = ObjectId()

    # update_project success
    projects.insert_one({"_id": ObjectId(), "name": "Old"})
    uid = str(projects.documents[-1]["_id"])
    grant_project_role(uid, lead_id)
    from app.models.project import ProjectCreate

    resp3 = project_mod.update_project(
        ProjectCreate(name="New", code="CC"),
        uid,
        current_user={"_id": lead_id, "role": "super_admin"},
    )

    assert resp3["message"] == "Project updated"

    # get_project success for a project member
    uid2 = ObjectId()
    projects.insert_one({"_id": ObjectId()})
    grant_project_role(projects.documents[-1]["_id"], uid2, "reporter")
    res_get = project_mod.get_project(
        str(projects.documents[-1]["_id"]),
        current_user={"_id": uid2, "role": "super_admin"},
    )
    assert "project" in res_get

    # delete success
    d = projects.insert_one({"_id": ObjectId()}).inserted_id
    grant_project_role(d, lead_id)
    resp4 = project_mod.delete_project(
        str(d), current_user={"_id": lead_id, "role": "super_admin"}
    )
    assert resp4["message"] == "Project archived successfully"


def test_assign_statuses_to_project_too_few(
    monkeypatch, fake_collection, grant_project_role
):
    projects = fake_collection.__class__([{"_id": ObjectId()}])
    statuses = fake_collection.__class__()
    monkeypatch.setattr(project_mod, "projects_collection", projects)
    monkeypatch.setattr(project_mod, "statuses_collection", statuses)

    from app.models.project import ProjectStatusAssign

    lead_id = ObjectId()
    grant_project_role(projects.documents[0]["_id"], lead_id)

    with pytest.raises(HTTPException):
        project_mod.assign_statuses_to_project(
            str(projects.documents[0]["_id"]),
            ProjectStatusAssign(status_ids=[str(ObjectId())]),
            current_user={"_id": lead_id, "role": "super_admin"},
        )


def test_ticket_update_missing_and_unauthorized_and_filters(
    monkeypatch, fake_collection, grant_project_role
):
    tickets = fake_collection.__class__()
    comments = fake_collection.__class__()
    notifications = fake_collection.__class__()
    projects = fake_collection.__class__()
    users = fake_collection.__class__()

    monkeypatch.setattr(ticket_mod, "ticket_collection", tickets)
    monkeypatch.setattr(ticket_mod, "comments_collection", comments)
    monkeypatch.setattr(ticket_mod, "notifications_collection", notifications)
    monkeypatch.setattr(ticket_mod, "projects_collection", projects)
    monkeypatch.setattr(ticket_mod, "user_collection", users)

    # update missing
    with pytest.raises(HTTPException):
        ticket_mod.update_ticket(
            str(ObjectId()),
            TicketUpdate(priority=Priority.LOW, category=Category.BUG),
            current_user={"_id": ObjectId(), "role": "agent"},
        )

    # insert and unauthorized update
    tid = tickets.insert_one({"_id": ObjectId(), "userId": "owner"}).inserted_id
    with pytest.raises(HTTPException):
        ticket_mod.update_ticket(
            str(tid),
            TicketUpdate(priority=Priority.LOW, category=Category.BUG),
            current_user={"_id": ObjectId(), "role": "agent"},
        )

    # get_tickets with filters: status/priority/search/sort asc and non-super_admin
    project_key = "BCX"
    project_id = projects.insert_one(
        {"_id": ObjectId(), "code": project_key}
    ).inserted_id
    uid = ObjectId()
    grant_project_role(project_id, uid, "member")
    tickets.insert_one(
        {
            "_id": ObjectId(),
            "project_id": str(project_id),
            "project_key": project_key,
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
        project_key,
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

    # additional branches: get_tickets category and assigned_agent
    tickets.insert_one(
        {
            "_id": ObjectId(),
            "project_id": str(project_id),
            "project_key": project_key,
            "createdAt": datetime.now(timezone.utc),
            "priorityOrder": 1,
            "dueDate": datetime.now(timezone.utc),
            "userId": str(uid),
            "category": "support",
            "assigned_agent": "ag1",
        }
    )
    res_f = ticket_mod.get_tickets(
        project_key,
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


# registration extra branches
def test_register_admin_always_creates_admin(monkeypatch, fake_collection):
    from app.api import registration

    users = fake_collection.__class__()
    monkeypatch.setattr(registration, "user_collection", users)
    monkeypatch.setattr(registration, "hash_password", lambda value: value)

    payload = registration.AdminRegister(
        name="X",
        email="x@example.com",
        password="registration-password",
    )

    registration.register_admin(
        payload,
        current_user={"role": "super_admin"},
    )

    assert users.documents[0]["role"] == "admin"


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


def test_ticket_comments_conversion_and_filters(
    monkeypatch, fake_collection, grant_project_role
):
    tickets = fake_collection.__class__()
    comments = fake_collection.__class__()
    notifications = fake_collection.__class__()
    projects = fake_collection.__class__()
    users = fake_collection.__class__()

    monkeypatch.setattr(ticket_mod, "ticket_collection", tickets)
    monkeypatch.setattr(ticket_mod, "comments_collection", comments)
    monkeypatch.setattr(ticket_mod, "notifications_collection", notifications)
    monkeypatch.setattr(ticket_mod, "projects_collection", projects)
    monkeypatch.setattr(ticket_mod, "user_collection", users)

    # comments conversion
    detail_project_id = ObjectId()
    detail_user_id = ObjectId()
    tid = tickets.insert_one(
        {"_id": ObjectId(), "project_id": str(detail_project_id), "userId": "u"}
    ).inserted_id
    grant_project_role(detail_project_id, detail_user_id)
    comments.insert_one({"_id": ObjectId(), "ticket_id": str(tid), "text": "c"})
    resp = ticket_mod.get_ticket_details(
        current_user={"_id": detail_user_id, "role": "super_admin"},
        ticket_id=str(tid),
    )
    assert resp["comments"][0]["_id"] == str(comments.documents[0]["_id"])

    # get_tickets status/priority lower branches
    project_key = "BB"
    uid = ObjectId()
    list_project_id = projects.insert_one(
        {"_id": ObjectId(), "code": project_key}
    ).inserted_id
    grant_project_role(list_project_id, uid, "member")
    tickets.insert_one(
        {
            "_id": ObjectId(),
            "project_id": str(list_project_id),
            "project_key": project_key,
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
        project_key,
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
    projects = fake_collection.__class__()
    monkeypatch.setattr(status_mod, "statuses_collection", statuses)
    monkeypatch.setattr(status_mod, "projects_collection", projects)

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


def test_assign_statuses_project_not_found_and_status_not_found(
    monkeypatch, fake_collection, grant_project_role
):
    projects = fake_collection.__class__()
    statuses = fake_collection.__class__()
    monkeypatch.setattr(project_mod, "projects_collection", projects)
    monkeypatch.setattr(project_mod, "statuses_collection", statuses)

    from app.models.project import ProjectStatusAssign

    # project not found
    with pytest.raises(HTTPException):
        project_mod.assign_statuses_to_project(
            str(ObjectId()),
            ProjectStatusAssign(status_ids=[str(ObjectId()), str(ObjectId())]),
            current_user={"_id": ObjectId(), "role": "super_admin"},
        )

    # create project and one missing status -> should 404 when checking statuses
    b = projects.insert_one({"_id": ObjectId()}).inserted_id
    lead_id = ObjectId()
    grant_project_role(b, lead_id)
    with pytest.raises(HTTPException):
        project_mod.assign_statuses_to_project(
            str(b),
            ProjectStatusAssign(status_ids=[str(ObjectId()), str(ObjectId())]),
            current_user={"_id": lead_id, "role": "super_admin"},
        )


def test_ticket_lower_filters_and_project_status_validation(
    monkeypatch, fake_collection, grant_project_role
):
    tickets = fake_collection.__class__()
    projects = fake_collection.__class__()
    monkeypatch.setattr(ticket_mod, "ticket_collection", tickets)
    monkeypatch.setattr(ticket_mod, "projects_collection", projects)

    project_key = "ZC"
    project_id = projects.insert_one(
        {"_id": ObjectId(), "code": project_key}
    ).inserted_id
    uid = ObjectId()
    grant_project_role(project_id, uid, "reporter")
    # add ticket belonging to someone else
    tickets.insert_one(
        {
            "_id": ObjectId(),
            "project_id": str(project_id),
            "project_key": project_key,
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
        project_key,
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
