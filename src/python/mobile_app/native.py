"""Request correlation and cancellation for the in-process Swift bridge."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable


class NativeRequests:
    """Correlate asynchronous native operations with Pipecat processing tasks."""

    def __init__(self, emit: Callable[[dict], None], timeout: float = 90):
        self.emit = emit
        self.timeout = timeout
        self.pending: dict[str, asyncio.Queue] = {}

    async def stream(self, operation: str, turn: str, **payload) -> AsyncIterator[dict]:
        request = uuid.uuid4().hex
        queue = self.pending[request] = asyncio.Queue(maxsize=1024)
        self.emit(
            {"type": "request", "operation": operation, "request": request, "turn": turn, **payload}
        )
        finished = False
        try:
            # Idle timeout is renewed for every chunk. Playback has its own ack.
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=self.timeout)
                except TimeoutError as exc:
                    raise TimeoutError(f"Native {operation} timed out. Please try again.") from exc
                if event.get("error"):
                    raise RuntimeError(event["error"])
                if event.get("done"):
                    finished = True
                    return
                yield event
        finally:
            self.pending.pop(request, None)
            if not finished:
                self.emit({"type": "cancel_request", "request": request, "turn": turn})

    def resolve(self, event: dict):
        queue = self.pending.get(event.get("request"))
        if queue is not None:
            queue.put_nowait(event)
