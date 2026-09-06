"""Compose and run the embedded Pipecat voice bot."""

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from pipecat.frames.frames import InterruptionFrame, TranscriptionFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_text_processor import LLMTextProcessor
from pipecat.utils.text.simple_text_aggregator import SimpleTextAggregator
from pipecat.workers.runner import WorkerRunner

from .native import NativeRequests
from .processors.foundation_models import FoundationModelProcessor
from .processors.native_asr import NativeASRInput
from .processors.phonon import PhononProcessor
from .processors.playback_context import PlaybackContextProcessor
from .state import ConversationState, Turn, tag_frame

INSTRUCTIONS = (
    "You are Pipecat, a friendly voice assistant. Respond in English using one or two "
    "short sentences, at most 80 words. Use plain spoken language without markdown. "
    "Be useful and direct. Do not claim to perform actions or access live information. "
    "You are running entirely on-device, using Apple Foundational models for ASR and the LLM, "
    "and Gradium Phonon for speech-to-text. You can answer with that if asked about how you "
    "are built"
)


class VoiceAgent:
    """A single embedded session; native callbacks enter via receive()."""

    def __init__(
        self, emit: Callable[[dict], None], *, sentence_boundary_matcher: Callable[[str], int]
    ):
        self.emit = emit
        self.native = NativeRequests(emit)
        self.ready = asyncio.Event()
        self.state = ConversationState(emit)
        self.text_aggregator = SimpleTextAggregator(
            sentence_boundary_matcher=sentence_boundary_matcher, max_buffer_chars=420
        )
        self.worker = PipelineWorker(
            Pipeline(
                [
                    NativeASRInput(self.state),
                    FoundationModelProcessor(self.state, self.native, INSTRUCTIONS),
                    LLMTextProcessor(text_aggregator=self.text_aggregator),
                    PhononProcessor(self.state, self.native),
                    PlaybackContextProcessor(self.state),
                ]
            ),
            params=PipelineParams(),
            enable_rtvi=False,
            enable_turn_tracking=False,
            idle_timeout_secs=None,
        )
        self.runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)

        @self.worker.event_handler("on_pipeline_started")
        async def started(worker, frame):
            self.ready.set()
            self.emit({"type": "ready"})

        @self.worker.event_handler("on_pipeline_error")
        async def error(worker, frame):
            await self.fail(frame.metadata.get("turn"), frame.error)

    @property
    def history(self) -> list[dict[str, str]]:
        return self.state.context.messages

    @property
    def turn(self) -> Turn | None:
        return self.state.turn

    async def interrupt(self) -> None:
        old = self.turn.id if self.turn else None
        self.state.commit()
        self.emit({"type": "cancel_turn", "turn": old})
        await self.worker.queue_frame(InterruptionFrame())

    async def fail(self, turn: str | None, message: str) -> None:
        if turn is None or self.state.is_current(turn):
            await self.interrupt()
            self.emit({"type": "error", "message": message, "turn": turn})

    async def receive(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "result":
            self.native.resolve(event)
        elif kind == "transcription":
            text = str(event.get("text", "")).strip()
            if not text:
                return
            if len(text) > 2000:
                raise ValueError("Please keep each turn under 2,000 characters.")
            if self.turn:
                await self.interrupt()
            turn = str(event.get("turn") or uuid.uuid4().hex)
            self.state.turn = Turn(turn, text)
            self.emit({"type": "state", "state": "thinking", "turn": turn})
            await self.worker.queue_frame(
                tag_frame(
                    TranscriptionFrame(
                        text=text,
                        user_id="local-user",
                        timestamp=datetime.now(UTC).isoformat(),
                        finalized=True,
                    ),
                    turn,
                )
            )
        elif kind in {"interrupt", "reset", "stop"}:
            await self.interrupt()
            if kind == "reset":
                self.state.context.set_messages([])
        else:
            raise ValueError(f"Unknown native event: {kind}")

    async def run(self) -> None:
        await self.runner.add_workers(self.worker)
        await self.runner.run()

    async def close(self) -> None:
        await self.interrupt()
        await self.runner.cancel()
