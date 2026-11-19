"""Publish the finite sample updates while the worker can still read cancellation."""
from collections.abc import Callable
from threading import Event, Lock, Thread

from contracts.generated.protocol import MarketBarUpdateEvent, MarketSubscriptionRequest, Message
from providers.simulated import BarUpdate


class Replay:
    def __init__(self, emit: Callable[[Message], None], message_id: Callable[[], str],
                 now_ms: Callable[[], int]):
        self.emit = emit
        self.message_id = message_id
        self.now_ms = now_ms
        self.request_id: str | None = None
        self.lock = Lock()
        self.stop: Event | None = None
        self.thread: Thread | None = None

    def active_id(self) -> str | None:
        with self.lock:
            return self.request_id

    def start(self, request: MarketSubscriptionRequest, events: list[BarUpdate]) -> None:
        self.close()
        self.request_id = request['request_id']
        self.stop = Event()
        self.thread = Thread(target=self.publish, args=(request, events, self.stop))
        self.thread.start()

    def publish(self, request: MarketSubscriptionRequest, updates: list[BarUpdate], stop: Event) -> None:
        source = request['payload']
        after = source['after']
        for sequence, update in enumerate(updates, after['sequence'] + 1):
            # Keep both observations separately visible in the development chart.
            if stop.wait(0.6):
                return
            event: MarketBarUpdateEvent = {
                'protocol_version': '1.1', 'message_id': self.message_id(), 'timestamp_ms': self.now_ms(),
                'type': 'event', 'event': 'market.bar_update',
                'stream_id': after['stream_id'], 'epoch': after['epoch'], 'sequence': sequence,
                'payload': {'series': source['series'], 'dataset_revision': source['snapshot']['dataset_revision'],
                            'observed_at_ms': update['observed_at_ms'], 'bar': update['bar']},
            }
            with self.lock:
                if stop.is_set():
                    return
                self.emit(event)
                if update is updates[-1]:
                    self.request_id = None

    def cancel(self, request_id: str) -> bool:
        with self.lock:
            if self.request_id != request_id:
                return False
            assert self.stop is not None
            self.stop.set()
            self.request_id = None
        self.close()
        return True

    def close(self) -> None:
        if self.stop is not None and self.thread is not None:
            self.stop.set()
            self.thread.join()
        self.request_id, self.stop, self.thread = None, None, None
