"""In-process native requests, session fencing, and pipeline lifecycle."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any

from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMUpdateSettingsFrame,
    ManuallySwitchServiceFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.apple.context import bounded_history
from pipecat.services.apple.frames import AppleControlFrame, AppleInputFrame
from pipecat.services.settings import LLMSettings

if TYPE_CHECKING:
    from pipecat.pipeline.service_switcher import ServiceSwitcher
    from pipecat.pipeline.worker import PipelineWorker
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
    from pipecat.services.apple.llm import AppleFoundationLLMService
    from pipecat.transports.apple import AppleTransport


class AppleNativeBridge:
    """Connect an embedded Pipecat worker to a native Apple audio host."""

    def __init__(self, emit: Callable[[dict], None], *, timeout: float = 90) -> None:
        """Initialize the bridge.

        Args:
            emit: Deliver a JSON-compatible event to the native host.
            timeout: Maximum idle time for a native operation in seconds.
        """
        self.emit = emit
        self.timeout = timeout
        self.pending: dict[str, asyncio.Queue] = {}
        self.operations: dict[str, tuple[str, str | None]] = {}
        self.ready = asyncio.Event()
        self.worker = None
        self.transport = None
        self.aggregators = None
        self.context = None
        self.tts: ServiceSwitcher | None = None
        self.llm: AppleFoundationLLMService | None = None
        self._system_instruction_factory: Callable[[dict[str, Any]], str] | None = None
        self.session: str | None = None
        self.turn: str | None = None
        self.input_epoch = 0
        self._turn_sessions: dict[str, str | None] = {}
        self.active = False
        self.muted = False

    def bind(
        self,
        worker: "PipelineWorker",
        transport: "AppleTransport",
        context: "LLMContext",
        aggregators: "LLMContextAggregatorPair",
        *,
        tts: "ServiceSwitcher | None" = None,
        llm: "AppleFoundationLLMService | None" = None,
        system_instruction_factory: Callable[[dict[str, Any]], str] | None = None,
    ) -> None:
        """Attach pipeline components and publish readiness and errors.

        Args:
            worker: Worker owning the native pipeline.
            transport: Native input and output transport.
            context: Shared conversation context.
            aggregators: Standard user and assistant context aggregators.
            tts: Optional native speech service switcher.
            llm: Optional Apple language model service.
            system_instruction_factory: Build replacement instructions from each
                native start event. Requires ``llm`` and preserves conversation history.
        """
        if system_instruction_factory is not None and llm is None:
            raise ValueError("Session instructions require an Apple LLM service")
        self.worker, self.transport = worker, transport
        self.context, self.aggregators = context, aggregators
        self.tts = tts
        self.llm = llm
        self._system_instruction_factory = system_instruction_factory

        @worker.event_handler("on_pipeline_started")
        async def started(worker, frame):
            self.ready.set()
            self.emit({"type": "ready"})

        @worker.event_handler("on_pipeline_error")
        async def failed(worker, frame):
            turn = frame.metadata.get("turn")
            session = frame.metadata.get("session")
            if session != self.session or (turn is not None and turn != self.turn):
                return
            self.emit(
                {
                    "type": "error",
                    "message": frame.error,
                    "turn": turn,
                    "session": session,
                    "operation": frame.metadata.get("operation"),
                    "recoverable": bool(frame.metadata.get("recoverable"))
                    and not frame.fatal
                    and (frame.processor is None or frame.processor.is_usable),
                }
            )

        async def tag_origin(processor: FrameProcessor, frame: Frame) -> None:
            turn = frame.metadata.setdefault("turn", self.turn)
            frame.metadata.setdefault("session", self._turn_sessions.get(turn, self.session))

        def register_origin(processor: FrameProcessor) -> None:
            processor.add_event_handler("on_before_push_frame", tag_origin)
            for child in processor.processors:
                register_origin(child)

        register_origin(worker.pipeline)

        @aggregators.assistant().event_handler("on_after_process_frame")
        async def response_processed(processor, frame: Frame) -> None:
            if self.llm is not None:
                self.llm.tool_result_committed(frame)
            if isinstance(frame, (LLMFullResponseEndFrame, InterruptionFrame)):
                self._trim_context()
                if (
                    self.llm is not None
                    and frame.metadata.get("session") == self.session
                    and frame.metadata.get("turn") == self.turn
                ):
                    self.llm.prepare_context(self.context)
            if isinstance(frame, LLMFullResponseEndFrame):
                turn = frame.metadata.get("turn")
                if (
                    self.active
                    and turn == self.turn
                    and frame.metadata.get("session") == self.session
                ):
                    self.emit(
                        {
                            "type": "state",
                            "state": "listening",
                            "turn": turn,
                            "session": self.session,
                        }
                    )

    def _trim_context(self) -> None:
        # Keep the shared universal context bounded as well as the native model
        # request. Retain complete recent turns, including played interruptions.
        messages = list(self.context.messages)
        instructions = messages[:1] if messages and messages[0].get("role") == "system" else []
        self.context.set_messages(instructions + bounded_history(messages[len(instructions) :]))

    def begin_turn(self) -> str:
        """Assign identity before a native speech proposal reaches the pipeline."""
        self.turn = uuid.uuid4().hex
        self._turn_sessions[self.turn] = self.session
        if len(self._turn_sessions) > 64:
            self._turn_sessions.pop(next(iter(self._turn_sessions)))
        self.emit({"type": "user_turn", "turn": self.turn, "session": self.session})
        return self.turn

    async def stream(self, operation: str, turn: str | None, **payload: Any) -> AsyncIterator[dict]:
        """Run a native operation with bounded waiting and cancellation."""
        request = uuid.uuid4().hex
        session = self._turn_sessions.get(turn, self.session)
        queue = self.pending[request] = asyncio.Queue(maxsize=1024)
        self.operations[request] = (operation, turn)
        self.emit(
            {
                "type": "request",
                "operation": operation,
                "request": request,
                "turn": turn,
                "session": session,
                **payload,
            }
        )
        finished = False
        try:
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
            self.operations.pop(request, None)
            if not finished:
                self.emit(
                    {
                        "type": "cancel_request",
                        "request": request,
                        "turn": turn,
                        "session": session,
                    }
                )

    async def receive(self, event: dict) -> None:
        """Route native callbacks into the worker without blocking on inference."""
        kind = event.get("type")
        if kind == "result":
            if queue := self.pending.get(event.get("request")):
                _, turn = self.operations[event["request"]]
                session = self._turn_sessions.get(turn, self.session)
                if event.get("session", session) != session or event.get("turn", turn) != turn:
                    return
                queue.put_nowait(event)
            return
        if self.worker is None:
            raise RuntimeError("The Apple bridge is not attached to a pipeline")
        if kind == "start":
            session = event["session"]
            instruction = (
                self._system_instruction_factory(dict(event))
                if self._system_instruction_factory is not None
                else None
            )
            if self._system_instruction_factory is not None and not isinstance(instruction, str):
                raise TypeError("Session instructions must be a string")
            await self._select_tts(event.get("provider", "pocket-tts"))
            self.active = False
            if instruction is not None:
                # A settings control frame cannot overtake an old generation.
                # Interrupt it before awaiting the replacement session's settings.
                interrupted = InterruptionFrame()
                interrupted.metadata.update(turn=self.turn, session=self.session)
                await self._process_control(self.llm, interrupted)
            self.session = session
            self.turn = None
            self.muted = False
            if instruction is not None:
                settings = LLMUpdateSettingsFrame(
                    delta=LLMSettings(system_instruction=instruction), service=self.llm
                )
                settings.metadata.update(turn=None, session=session)
                await self._process_control(self.llm, settings)
            self.active = True
        elif event.get("session") is not None and event["session"] != self.session:
            return
        if kind in {"start", "stop", "reset", "mute", "unmute", "interrupt"}:
            self.input_epoch += 1
        if kind == "playback":
            await self.transport.output().playback_changed(event)
            return
        if kind in {"vad", "transcription", "end_turn"} and (not self.active or self.muted):
            return
        if kind == "stop":
            self.active = False
        elif kind == "mute":
            self.muted = True
        elif kind == "unmute":
            self.muted = False
        frame_type = AppleInputFrame if kind == "transcription" else AppleControlFrame
        frame = frame_type(dict(event))
        frame.metadata.update(input_epoch=self.input_epoch, session=self.session, turn=self.turn)
        await self.worker.queue_frame(frame)

    async def _select_tts(self, provider: str) -> None:
        """Cancel the previous provider before selecting a new session's service."""
        if self.tts is None:
            return
        service = next(
            (
                service
                for service in self.tts.services
                if getattr(service, "provider_id", None) == provider
            ),
            None,
        )
        if service is None:
            raise ValueError(f"Unknown native TTS provider: {provider}")
        self.active = False
        # The switcher only routes interruptions to its active branch. Await
        # that service's cancellation before changing the routing decision.
        interrupted = InterruptionFrame()
        interrupted.metadata.update(turn=self.turn, session=self.session)
        await self._process_control(self.tts.strategy.active_service, interrupted)
        await self._process_control(self.tts, ManuallySwitchServiceFrame(service=service))
        if self.tts.strategy.active_service is not service:
            raise RuntimeError(f"Native TTS provider is unavailable: {provider}")

    async def _process_control(self, processor: FrameProcessor, frame: Frame) -> None:
        """Wait for a queued control frame to finish before admitting native input."""
        completed = asyncio.Event()

        async def processed(
            processor: FrameProcessor, frame: Frame, direction: FrameDirection
        ) -> None:
            completed.set()

        await processor.queue_frame(frame, callback=processed)
        await asyncio.wait_for(completed.wait(), timeout=self.timeout)

    async def endpoint(self, turn: str | None, *, endpoint_id: str | None = None) -> None:
        """Order an identified endpoint after all native ASR transcript callbacks.

        Args:
            turn: Current conversation turn, or None before speech is confirmed.
            endpoint_id: Identity of the acoustic span being finalized, allowing
                the STT service to reject a completion queued before resumed speech.
        """
        input_epoch, session = self.input_epoch, self.session
        async for _ in self.stream("finalize_asr", turn):
            pass
        if (
            self.active
            and not self.muted
            and self.turn == turn
            and self.input_epoch == input_epoch
            and self.session == session
        ):
            frame = AppleInputFrame(
                {
                    "type": "endpoint",
                    "turn": turn,
                    "session": session,
                    "endpoint_id": endpoint_id,
                }
            )
            frame.metadata.update(input_epoch=input_epoch, session=session, turn=turn)
            await self.worker.queue_frame(frame)
