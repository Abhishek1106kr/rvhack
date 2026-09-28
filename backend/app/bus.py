"""In-process event bus. Synchronous fan-out keeps delivery order deterministic."""

import logging
from collections import defaultdict
from collections.abc import Callable

from app.events import Error, Event

log = logging.getLogger(__name__)

Subscriber = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []
        self._seq: defaultdict[str, int] = defaultdict(int)

    def subscribe(self, subscriber: Subscriber) -> Callable[[], None]:
        """Register a subscriber. Subscribers must not block; queue work instead."""
        self._subscribers.append(subscriber)
        return lambda: self._subscribers.remove(subscriber)

    def publish(self, event: Event) -> Event:
        """Stamp the per-session sequence number and deliver to every subscriber.

        A failing subscriber never stops delivery to the others or breaks the session;
        the failure is published as an ERROR event.
        """
        self._seq[event.session_id] += 1
        event = event.model_copy(update={"seq": self._seq[event.session_id]})

        failures: list[str] = []
        for subscriber in list(self._subscribers):
            try:
                subscriber(event)
            except Exception as exc:
                log.exception("event subscriber failed on %s", event.event_type)
                failures.append(f"{type(exc).__name__}: {exc}")

        # Never report failures while delivering a bus error, or a subscriber that
        # always fails would recurse forever.
        if failures and not (isinstance(event, Error) and event.stage == "bus"):
            for failure in failures:
                self.publish(
                    Error(
                        session_id=event.session_id,
                        turn_id=event.turn_id,
                        stage="bus",
                        message=f"subscriber failed on {event.event_type}: {failure}",
                        recoverable=True,
                    )
                )
        return event
