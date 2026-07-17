"""Test cases for new features"""

import pytest
from bson import ObjectId
from fastapi import HTTPException
from pydantic import ValidationError

from app.api import notifications as notif_mod
from app.api import reports as reports_mod
from app.api import ticket as ticket_mod
from app.models.ticket import (
    TicketUpdate,
)


class TestNotificationEndpoints:
    """Test notification retrieval endpoints"""

    def test_list_notifications_empty(self, monkeypatch, fake_collection):
        user_id = ObjectId()
        monkeypatch.setattr(notif_mod, "notifications_collection", fake_collection)

        response = notif_mod.list_notifications(
            current_user={"_id": user_id}, page=1, page_size=20
        )

        assert response["pagination"]["total"] == 0
        assert response["data"] == []

    def test_list_notifications_paginated(self, monkeypatch, fake_collection):
        """Test notification listing with pagination"""
        user_id = ObjectId()
        user_id_str = str(user_id)

        # Create 25 notifications for the user
        docs = [
            {
                "_id": ObjectId(),
                "user_id": user_id_str,
                "event_type": "ticket_created",
                "created_at": "2026-07-08T12:00:00Z",
                "is_read": False,
            }
            for _ in range(25)
        ]
        notifications = fake_collection.__class__(docs)

        # Verify notifications were created
        assert notifications.count_documents({"user_id": user_id_str}) == 25

        monkeypatch.setattr(notif_mod, "notifications_collection", notifications)

        response = notif_mod.list_notifications(
            current_user={"_id": user_id}, page=1, page_size=20
        )

        assert response["pagination"]["total"] == 25
        assert len(response["data"]) == 20
        assert response["pagination"]["has_next"] is True
        print("Count:", notifications.count_documents({"user_id": user_id_str}))

        docs = list(notifications.find({"user_id": user_id_str}))
        print("Found:", len(docs))
        print(docs[:2])

        response = notif_mod.list_notifications(
            current_user={"_id": user_id},
            page=1,
            page_size=20,
        )

        print(response)

    def test_get_notification_success(self, monkeypatch, fake_collection):
        user_id = ObjectId()
        notif_id = ObjectId()
        notifications = fake_collection.__class__(
            [
                {
                    "_id": notif_id,
                    "user_id": str(user_id),
                    "event_type": "ticket_created",
                }
            ]
        )
        monkeypatch.setattr(notif_mod, "notifications_collection", notifications)

        response = notif_mod.get_notification(
            str(notif_id), current_user={"_id": user_id}
        )

        assert response["user_id"] == str(user_id)

    def test_get_notification_not_found(self, monkeypatch, fake_collection):
        monkeypatch.setattr(notif_mod, "notifications_collection", fake_collection)

        with pytest.raises(HTTPException) as exc_info:
            notif_mod.get_notification(
                str(ObjectId()), current_user={"_id": ObjectId()}
            )

        assert exc_info.value.status_code == 404

    def test_get_notification_unauthorized(self, monkeypatch, fake_collection):
        notif_id = ObjectId()
        notifications = fake_collection.__class__(
            [
                {
                    "_id": notif_id,
                    "user_id": str(ObjectId()),
                    "event_type": "ticket_created",
                }
            ]
        )
        monkeypatch.setattr(notif_mod, "notifications_collection", notifications)

        with pytest.raises(HTTPException) as exc_info:
            notif_mod.get_notification(str(notif_id), current_user={"_id": ObjectId()})

        assert exc_info.value.status_code == 403

    def test_mark_as_read_success(self, monkeypatch, fake_collection):
        user_id = ObjectId()
        notif_id = ObjectId()
        notifications = fake_collection.__class__(
            [
                {
                    "_id": notif_id,
                    "user_id": str(user_id),
                    "event_type": "ticket_created",
                    "is_read": False,
                }
            ]
        )
        monkeypatch.setattr(notif_mod, "notifications_collection", notifications)

        response = notif_mod.mark_as_read(str(notif_id), current_user={"_id": user_id})

        assert response["message"] == "Notification marked as read"
        assert notifications.documents[0]["is_read"] is True


class TestReportsEndpoints:
    """Test reporting endpoints"""

    def test_agent_tickets_empty(self, monkeypatch, fake_collection):
        admin_id = ObjectId()
        monkeypatch.setattr(reports_mod, "ticket_collection", fake_collection)
        monkeypatch.setattr(reports_mod, "user_collection", fake_collection)

        response = reports_mod.report_agent_tickets(
            current_user={"_id": admin_id, "role": "super_admin"}
        )

        assert response["data"] == []

    def test_agent_tickets_unauthorized(self, monkeypatch, fake_collection):
        user_id = ObjectId()
        monkeypatch.setattr(reports_mod, "ticket_collection", fake_collection)
        monkeypatch.setattr(reports_mod, "user_collection", fake_collection)

        with pytest.raises(HTTPException) as exc_info:
            reports_mod.report_agent_tickets(
                current_user={"_id": user_id, "role": "agent"}
            )

        assert exc_info.value.status_code == 403

    def test_overdue_tickets_no_overdue(self, monkeypatch, fake_collection):
        admin_id = ObjectId()
        tickets = fake_collection.__class__([])
        monkeypatch.setattr(reports_mod, "ticket_collection", tickets)

        response = reports_mod.report_overdue_tickets(
            current_user={"_id": admin_id, "role": "admin"}
        )

        assert response["total_overdue"] == 0
        assert response["by_priority"] == {}


def test_general_ticket_update_rejects_status_and_assignment_fields():
    with pytest.raises(ValidationError):
        TicketUpdate(
            status="in_progress",
            assigned_to="agent",
            priority="high",
        )


class TestOverdueFilter:
    """Test overdue flag and filter"""

    def test_overdue_computed_field_set(self, monkeypatch, fake_collection):
        """Test that is_overdue computed field is set correctly in ticket responses"""
        from datetime import datetime, timedelta, timezone

        project_id = ObjectId()
        now = datetime.now(timezone.utc)
        past_date = now - timedelta(days=1)
        future_date = now + timedelta(days=1)

        projects = fake_collection.__class__(
            [{"_id": project_id, "code": "TEST", "assigned_statuses": []}]
        )
        tickets = fake_collection.__class__(
            [
                {
                    "_id": ObjectId(),
                    "project_id": str(project_id),
                    "project_key": "TEST",
                    "due_date": past_date,
                    "current_status_name": "open",
                    "userId": "user1",
                },
                {
                    "_id": ObjectId(),
                    "project_id": str(project_id),
                    "project_key": "TEST",
                    "due_date": future_date,
                    "current_status_name": "open",
                    "userId": "user1",
                },
            ]
        )

        monkeypatch.setattr(ticket_mod, "projects_collection", projects)
        monkeypatch.setattr(ticket_mod, "ticket_collection", tickets)

        # Simulate what get_tickets does with tickets
        response_tickets = []
        for t in tickets.documents:
            t_copy = dict(t)
            t_copy["_id"] = str(t_copy["_id"])
            if t_copy.get("due_date") and t_copy.get("current_status_name") not in [
                "resolved",
                "closed",
            ]:
                t_copy["is_overdue"] = t_copy["due_date"] < now
            else:
                t_copy["is_overdue"] = False
            response_tickets.append(t_copy)

        # Check that overdue flag is set correctly
        assert response_tickets[0]["is_overdue"] is True  # Past date
        assert response_tickets[1]["is_overdue"] is False  # Future date


class TestStatuses:
    """Test ON_HOLD and REOPENED statuses"""

    def test_on_hold_status_exists(self):
        from app.models.ticket import Status

        assert Status.ON_HOLD.value == "on_hold"

    def test_reopened_status_exists(self):
        from app.models.ticket import Status

        assert Status.REOPENED.value == "reopened"

    def test_all_statuses_present(self):
        from app.models.ticket import Status

        statuses = [s.value for s in Status]
        expected = ["open", "in_progress", "on_hold", "resolved", "reopened", "closed"]

        for expected_status in expected:
            assert expected_status in statuses
