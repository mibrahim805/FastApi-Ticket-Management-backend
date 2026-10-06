import asyncio
from datetime import datetime, timezone

from bson import ObjectId

from app.api import auth
from app.api import notifications as notifications_api
from app.grpc import notification_pb2
from app.grpc.notification_service import NotificationService


def test_grpc_notification_stream_resumes_after_last_event(
    monkeypatch,
    fake_collection,
):
    user_id = ObjectId()
    first_id = ObjectId()
    second_id = ObjectId()
    notifications = fake_collection.__class__(
        [
            {
                "_id": first_id,
                "user_id": str(user_id),
                "ticket_id": "ticket-1",
                "event_type": "ticket_created",
                "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            },
            {
                "_id": second_id,
                "user_id": str(user_id),
                "ticket_id": "ticket-2",
                "event_type": "ticket_updated",
                "created_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
            },
        ]
    )
    monkeypatch.setattr(notifications_api, "notifications_collection", notifications)
    monkeypatch.setattr(auth, "get_current_user", lambda credentials: {"_id": user_id})

    class Context:
        def invocation_metadata(self):
            return (("authorization", "Bearer test-token"),)

        def done(self):
            return False

    async def read_event():
        stream = NotificationService(poll_interval_seconds=0).SubscribeNotifications(
            notification_pb2.SubscribeRequest(last_event_id=str(first_id)),
            Context(),
        )
        event = await anext(stream)
        await stream.aclose()
        return event

    event = asyncio.run(read_event())

    assert event.id == str(second_id)
    assert event.ticket_id == "ticket-2"
    assert event.event_type == "ticket_updated"
