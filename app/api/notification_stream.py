"""In-process notification fan-out for long-lived notification streams."""

import asyncio
import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class _Subscriber:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue


class NotificationSubscription:
    """Async queue subscription removed automatically when it is closed."""

    def __init__(self, broker: "NotificationBroker", user_id: str):
        self._broker = broker
        self.user_id = user_id
        self._subscriber = _Subscriber(
            loop=asyncio.get_running_loop(),
            queue=asyncio.Queue(),
        )
        self._closed = False

    async def get(self) -> dict:
        return await self._subscriber.queue.get()

    def get_nowait(self) -> dict:
        return self._subscriber.queue.get_nowait()

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._broker._unsubscribe(self.user_id, self._subscriber)

    async def __aenter__(self) -> "NotificationSubscription":
        self._broker._subscribe(self.user_id, self._subscriber)
        return self

    async def __aexit__(self, _type, _value, _traceback) -> None:
        await self.close()


class NotificationBroker:
    """Fan out inserted notifications without polling once per connection."""

    def __init__(self):
        self._subscribers: dict[str, set[_Subscriber]] = {}
        self._lock = threading.Lock()

    def subscribe(self, user_id: str) -> NotificationSubscription:
        return NotificationSubscription(self, user_id)

    def _subscribe(self, user_id: str, subscriber: _Subscriber) -> None:
        with self._lock:
            self._subscribers.setdefault(user_id, set()).add(subscriber)

    def _unsubscribe(self, user_id: str, subscriber: _Subscriber) -> None:
        with self._lock:
            subscribers = self._subscribers.get(user_id)
            if not subscribers:
                return
            subscribers.discard(subscriber)
            if not subscribers:
                self._subscribers.pop(user_id, None)

    def publish(self, notification: dict) -> None:
        """Publish from synchronous request code, regardless of its thread."""
        user_id = str(notification.get("user_id") or "")
        with self._lock:
            subscribers = tuple(self._subscribers.get(user_id, ()))

        for subscriber in subscribers:
            try:
                subscriber.loop.call_soon_threadsafe(
                    subscriber.queue.put_nowait,
                    dict(notification),
                )
            except RuntimeError:
                # The subscriber's event loop may have closed before cleanup
                # could remove it from the registry.
                self._unsubscribe(user_id, subscriber)


notification_broker = NotificationBroker()
