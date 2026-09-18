"""Apple Speech transcription and native speech activity turn boundaries."""

import asyncio
import math
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from pipecat.audio.vad.apple import AppleVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADState
from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    InterimTranscriptionFrame,
    ProposedUserStartedSpeakingFrame,
    ProposedUserStoppedSpeakingFrame,
    STTMetadataFrame,
    TranscriptionFrame,
    UserMuteStartedFrame,
    UserMuteStoppedFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.apple.bridge import AppleNativeBridge
from pipecat.services.apple.frames import AppleControlFrame, AppleInputFrame
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import STTService
from pipecat.transcriptions.language import Language
from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies


class AppleSpeechSTTService(STTService):
    """Confirm native VAD activity with Apple ASR before starting a user turn."""

    def __init__(
        self, bridge: AppleNativeBridge, *, vad_analyzer: AppleVADAnalyzer, **kwargs: Any
    ) -> None:
        """Initialize Apple speech recognition.

        Args:
            bridge: In-process Apple host bridge.
            vad_analyzer: Pipecat analyzer for native SoundAnalysis scores.
            **kwargs: Additional STTService configuration.
        """
        super().__init__(
            audio_passthrough=False,
            sample_rate=16000,
            ttfs_p99_latency=0,
            settings=STTSettings(model="apple-speech", language=Language.EN_US),
            **kwargs,
        )
        self.bridge = bridge
        self.vad = vad_analyzer
        self._speech_active = False
        self._acoustic_pending = False
        self._turn_open = False
        self._endpoint_task: asyncio.Task | None = None
        self._endpoint_id: str | None = None
        self._closing_turn: asyncio.Event | None = None
        self._buffered: list[dict] = []
        self._earliest_buffered_end = -math.inf
        self._finalized_until = -1.0
        self._wait_for_quiet = False

    def service_metadata_frame(self) -> STTMetadataFrame:
        """Advertise native endpoint proposals with no transcript timeout."""
        frame = super().service_metadata_frame()
        frame.user_turn_strategies = ExternalUserTurnStrategies(wait_for_transcript=False)
        return frame

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        """Reject Python PCM; the native SpeechAnalyzer owns recognition."""
        raise NotImplementedError("Apple Speech receives audio directly from the native engine")
        yield  # Declare the STTService async-generator interface.

    async def cleanup(self) -> None:
        """Cancel ASR finalization and release the VAD analyzer."""
        await self._cancel_endpoint()
        await self.vad.cleanup()
        await super().cleanup()

    async def _cancel_endpoint(self) -> None:
        self._endpoint_id = None
        if self._endpoint_task:
            await self.cancel_task(self._endpoint_task)
            self._endpoint_task = None

    async def _begin_speech(self) -> bool:
        session, input_epoch = self.bridge.session, self.bridge.input_epoch
        if self._closing_turn is not None:
            await self._closing_turn.wait()
        if (
            not self.bridge.active
            or self.bridge.muted
            or self.bridge.session != session
            or self.bridge.input_epoch != input_epoch
        ):
            return False
        await self._cancel_endpoint()
        if not self._speech_active:
            self._speech_active = True
            self._acoustic_pending = True
            await self.push_frame(
                VADUserStartedSpeakingFrame(start_secs=self.vad.params.start_secs)
            )
        await self._confirm_turn()
        return True

    async def _confirm_turn(self) -> None:
        """Announce a turn only after native ASR supplies a word in the speech span."""
        if not self._buffered:
            return
        if not self._turn_open:
            self.bridge.begin_turn()
            self._turn_open = True
            await self.push_frame(ProposedUserStartedSpeakingFrame())
        buffered, self._buffered = self._buffered, []
        for event in buffered:
            await self._transcription(event)

    async def _close_turn(self) -> None:
        """Close the ordered user turn before admitting a system-priority start."""
        closed = self._closing_turn = asyncio.Event()

        async def on_closed(
            processor: FrameProcessor, frame: Frame, direction: FrameDirection
        ) -> None:
            self._turn_open = False
            self._closing_turn = None
            closed.set()

        frame = ProposedUserStoppedSpeakingFrame()
        frame.metadata.update(turn=self.bridge.turn, session=self.bridge.session)
        await self.bridge.aggregators.user().queue_frame(frame, callback=on_closed)
        await closed.wait()

    async def _end_speech(self) -> None:
        if self._speech_active:
            self._speech_active = False
            await self.push_frame(VADUserStoppedSpeakingFrame(stop_secs=self.vad.params.stop_secs))
        if (self._turn_open or self._acoustic_pending) and self._endpoint_task is None:
            turn = self.bridge.turn
            session, input_epoch = self.bridge.session, self.bridge.input_epoch
            endpoint_id = self._endpoint_id = uuid.uuid4().hex

            async def finalize() -> None:
                try:
                    if self.bridge.session != session or self.bridge.input_epoch != input_epoch:
                        return
                    await self.bridge.endpoint(turn, endpoint_id=endpoint_id)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if (
                        self.bridge.session == session
                        and self.bridge.input_epoch == input_epoch
                        and self._endpoint_id == endpoint_id
                    ):
                        self._endpoint_task = None
                        self._endpoint_id = None
                        self._acoustic_pending = False
                        self._buffered.clear()
                        error = ErrorFrame(str(exc), exception=exc)
                        error.metadata.update(turn=turn, session=session)
                        await self.push_error_frame(error)

            self._endpoint_task = self.create_task(finalize(), "apple-asr-finalize")

    async def _transcription(self, event: dict[str, Any]) -> None:
        text = str(event.get("text", "")).strip()
        if not any(character.isalnum() for character in text):
            return
        end = float(event.get("end", 0))
        if not math.isfinite(end):
            raise ValueError("ASR segment timestamps must be finite")
        if end <= self._finalized_until or end < self._earliest_buffered_end:
            return
        if len(text) > 2000:
            raise ValueError("Please keep each turn under 2,000 characters.")
        if not self._turn_open:
            # ASR can precede the first classifier window or arrive only when
            # quiet audio is finalized. Keep it private until the turn is real.
            self._buffered.append(event)
            if len(self._buffered) > 32:
                raise RuntimeError("Speech activity detection did not produce a turn boundary")
            if self._speech_active and self._endpoint_task is None:
                await self._confirm_turn()
            return
        final = bool(event.get("final"))
        cls = TranscriptionFrame if final else InterimTranscriptionFrame
        frame = cls(text, "local-user", datetime.now(UTC).isoformat())
        frame.metadata["turn"] = self.bridge.turn
        if final:
            frame.finalized = True
            self._finalized_until = end
        await self.push_frame(frame)

    def _expire_early_transcripts(self, timestamp: float) -> None:
        """Discard text outside any possible upcoming classifier speech window."""
        if not self._turn_open and not self._acoustic_pending:
            # SoundAnalysis scores cover the preceding half second. An older
            # hypothesis cannot belong to a later, unrelated acoustic onset.
            self._earliest_buffered_end = max(self._earliest_buffered_end, timestamp - 0.5)
            self._buffered = [
                event for event in self._buffered if event["end"] >= self._earliest_buffered_end
            ]

    async def _reset_input(self) -> None:
        if self._closing_turn is not None:
            await self._closing_turn.wait()
        turn_was_open = self._turn_open
        await self._cancel_endpoint()
        if self._speech_active:
            await self.push_frame(VADUserStoppedSpeakingFrame(stop_secs=0))
        self._speech_active = False
        self._acoustic_pending = False
        self._turn_open = False
        self._buffered.clear()
        self._earliest_buffered_end = -math.inf
        self._finalized_until = -1.0
        self._wait_for_quiet = False
        self.vad.reset()
        await self.bridge.aggregators.user().reset()
        if turn_was_open:
            await self._close_turn()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Convert native scores and ASR callbacks into ordinary Pipecat frames.

        Args:
            frame: A native host event or standard pipeline frame.
            direction: Direction of frame processing.
        """
        if not isinstance(frame, (AppleInputFrame, AppleControlFrame)):
            await super().process_frame(frame, direction)
            return
        await FrameProcessor.process_frame(self, frame, direction)
        event = frame.event
        # A session can restart while an earlier callback is still queued.
        if event.get("session") is not None and event["session"] != self.bridge.session:
            return
        kind = event.get("type")
        if kind in {"vad", "transcription", "end_turn", "endpoint"} and (
            frame.metadata.get("input_epoch") != self.bridge.input_epoch
        ):
            # Mute/resume restarts ASR and VAD inside the same native session.
            return
        if kind == "vad":
            if not self.bridge.active or self.bridge.muted:
                return
            state = self.vad.analyze_confidence(
                event["confidence"], volume=event.get("volume", 1), timestamp=event["time"]
            )
            self._expire_early_transcripts(event["time"])
            if self._wait_for_quiet:
                if state == VADState.QUIET:
                    self._wait_for_quiet = False
                return
            if state == VADState.SPEAKING:
                await self._begin_speech()
            elif state == VADState.QUIET and self._speech_active:
                await self._end_speech()
        elif kind == "transcription":
            if self.bridge.active and not self.bridge.muted:
                await self._transcription(event)
        elif kind == "endpoint":
            if (
                event.get("turn") == self.bridge.turn
                and event.get("endpoint_id") == self._endpoint_id
                and not self._speech_active
                and self._endpoint_task is not None
            ):
                self._endpoint_task = None
                self._endpoint_id = None
                self._acoustic_pending = False
                # Finalization has acknowledged all native results. Starting
                # now cannot cancel the request that supplied these words.
                await self._confirm_turn()
                if self._turn_open:
                    await self._close_turn()
        elif kind == "end_turn":
            if not self._turn_open and self._buffered:
                if not await self._begin_speech():
                    return
            self._wait_for_quiet = self._speech_active
            await self._end_speech()
        elif kind in {"start", "stop", "reset", "mute", "unmute", "interrupt"}:
            await self._reset_input()
            if kind in {"stop", "reset", "interrupt", "start"}:
                old = self.bridge.turn
                await self.broadcast_interruption()
                self.bridge.emit(
                    {"type": "cancel_turn", "turn": old, "session": self.bridge.session}
                )
                self.bridge.turn = None
                if kind == "interrupt":
                    self.bridge.emit(
                        {
                            "type": "state",
                            "state": "listening",
                            "turn": old,
                            "session": self.bridge.session,
                        }
                    )
            if kind == "reset":
                await self.push_frame(AppleInputFrame({"type": "reset_context"}))
            elif kind in {"mute", "unmute"}:
                await self.push_frame(
                    UserMuteStartedFrame() if kind == "mute" else UserMuteStoppedFrame()
                )
        elif kind == "reset_context":
            await self.push_frame(frame, direction)
        else:
            raise ValueError(f"Unknown Apple input event: {kind}")
