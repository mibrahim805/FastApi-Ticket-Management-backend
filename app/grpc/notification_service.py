"""Authenticated server-streaming gRPC notification service."""

import asyncio
from datetime import datetime, timezone
from typing import Optional

import grpc
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api import auth
from app.api import notifications as notifications_api
from app.grpc import notification_pb2, notification_pb2_grpc


def _metadata_authorization(context: grpc.aio.ServicerContext) -> Optional[str]:
    metadata = dict(context.invocation_metadata())
    return metadata.get("authorization")


async def _authenticate(context: grpc.aio.ServicerContext) -> dict:
    authorization = _metadata_authorization(context) or ""
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token:
        await context.abort(
            grpc.StatusCode.UNAUTHENTICATED,
            "Authorization metadata must contain a Bearer token",
        )

    try:
        return auth.get_current_user(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        )
    except HTTPException as error:
        code = (
            grpc.StatusCode.UNAUTHENTICATED
            if error.status_code in {401, 403}
            else grpc.StatusCode.NOT_FOUND
        )
        await context.abort(code, str(error.detail))
    except (TypeError, ValueError):
        await context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid credentials")

    raise AssertionError("context.abort must terminate the RPC")


def _as_iso8601(value) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value or "")


def _to_proto(notification: dict) -> notification_pb2.Notification:
    return notification_pb2.Notification(
        id=str(notification["_id"]),
        project_id=str(notification.get("project_id") or ""),
        comment_id=str(notification.get("comment_id") or ""),
        ticket_id=str(notification.get("ticket_id") or ""),
        user_id=str(notification.get("user_id") or ""),
        actor_id=str(notification.get("actor_id") or ""),
        event_type=str(notification.get("event_type") or ""),
        created_at=_as_iso8601(notification.get("created_at")),
        is_read=bool(notification.get("is_read", False)),
        read_at=_as_iso8601(notification.get("read_at")),
    )


class NotificationService(notification_pb2_grpc.NotificationServiceServicer):
    def __init__(self, poll_interval_seconds: float = 1.0):
        self.poll_interval_seconds = poll_interval_seconds

    async def SubscribeNotifications(self, request, context):
        user = await _authenticate(context)
        user_id = str(user["_id"])
        last_event_id = request.last_event_id or None
        cursor = await asyncio.to_thread(
            notifications_api._get_stream_start_cursor,
            user_id,
            last_event_id,
        )

        while not context.done():
            new_notifications = await asyncio.to_thread(
                notifications_api._find_notifications_after,
                user_id,
                cursor,
            )
            if new_notifications:
                for notification in new_notifications:
                    if context.done():
                        return
                    cursor = notifications_api._notification_cursor(notification)
                    yield _to_proto(notification)
                continue

            await asyncio.sleep(self.poll_interval_seconds)


def create_grpc_server(host: str, port: int) -> grpc.aio.Server:
    server = grpc.aio.server()
    notification_pb2_grpc.add_NotificationServiceServicer_to_server(
        NotificationService(), server
    )
    bound_port = server.add_insecure_port(f"{host}:{port}")
    if bound_port == 0:
        raise RuntimeError(f"Unable to bind gRPC server to {host}:{port}")
    return server
