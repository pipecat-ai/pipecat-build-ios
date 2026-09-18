"""Native Apple transport and RTVI delivery for embedded voice applications."""

from typing import Any

from pydantic import BaseModel

from pipecat.frames.frames import (
    AggregatedTextFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    LLMMessagesUpdateFrame,
    StartFrame,
    TTSTextFrame,
)
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIObserver
from pipecat.services.apple.bridge import AppleNativeBridge
from pipecat.services.apple.frames import AppleInputFrame
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import BaseTransport, TransportParams


class AppleOutputTransport(BaseOutputTransport):
    """Represent native playback without moving PCM through Python."""

    def __init__(self, bridge: AppleNativeBridge, params: TransportParams, **kwargs: Any) -> None:
        """Initialize output.

        Args:
            bridge: Native event bridge.
            params: Transport parameters.
            **kwargs: Additional output transport configuration.
        """
        super().__init__(params, **kwargs)
        self.bridge = bridge
        self._speaking_request: str | None = None
        self._speaking_turn: str | None = None

    async def playback_changed(self, event: dict) -> None:
        """Translate actual playback start/drain callbacks into bot speech frames."""
        request = event.get("request")
        operation = self.bridge.operations.get(request)
        if event.get("speaking"):
            if not operation or operation[0] != "speak" or operation[1] != self.bridge.turn:
                return
            if self._speaking_request == request:
                return
            self._speaking_request, self._speaking_turn = request, operation[1]
            await self._speech_frame(BotStartedSpeakingFrame)
        elif request == self._speaking_request:
            await self._stop_speaking()

    async def _speech_frame(
        self, cls: type[BotStartedSpeakingFrame | BotStoppedSpeakingFrame]
    ) -> None:
        downstream, upstream = cls(), cls()
        downstream.broadcast_sibling_id = upstream.id
        upstream.broadcast_sibling_id = downstream.id
        downstream.metadata["turn"] = upstream.metadata["turn"] = self._speaking_turn
        await self.push_frame(downstream)
        await self.push_frame(upstream, FrameDirection.UPSTREAM)

    async def _stop_speaking(self) -> None:
        if self._speaking_request is not None:
            await self._speech_frame(BotStoppedSpeakingFrame)
            self._speaking_request = self._speaking_turn = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Forward native output and cancel its speech state on interruption.

        Args:
            frame: Incoming pipeline frame.
            direction: Direction of travel through the pipeline.
        """
        # Native audio has its own playback clock. BaseOutputTransport's PCM
        # MediaSender is deliberately not started for this transport.
        await FrameProcessor.process_frame(self, frame, direction)
        if isinstance(frame, StartFrame):
            await self.start(frame)
        elif isinstance(frame, (InterruptionFrame, EndFrame, CancelFrame)):
            await self._stop_speaking()
        elif isinstance(frame, AppleInputFrame) and frame.event.get("type") == "reset_context":
            await self.push_frame(LLMMessagesUpdateFrame(messages=[]))
            return
        await self.push_frame(frame, direction)


class AppleTransport(BaseTransport):
    """An in-process native host with standard Pipecat input/output processors."""

    def __init__(self, bridge: AppleNativeBridge, **kwargs: Any) -> None:
        """Initialize the transport.

        Args:
            bridge: Native host bridge.
            **kwargs: Additional BaseTransport configuration.
        """
        super().__init__(**kwargs)
        params = TransportParams(audio_out_sample_rate=24000, audio_in_sample_rate=16000)
        self._input = BaseInputTransport(params, name="AppleInputTransport")
        self._output = AppleOutputTransport(bridge, params)

    def input(self) -> BaseInputTransport:
        """Return the pipeline's native input processor."""
        return self._input

    def output(self) -> AppleOutputTransport:
        """Return the pipeline's native playback output processor."""
        return self._output


class AppleRTVIObserver(RTVIObserver):
    """Deliver standard RTVI messages over the in-process Apple bridge."""

    def __init__(self, bridge: AppleNativeBridge, **kwargs: Any) -> None:
        """Initialize native RTVI delivery.

        Args:
            bridge: Native event bridge.
            **kwargs: RTVIObserver parameters.
        """
        super().__init__(**kwargs)
        self.bridge = bridge
        self._event_turn: str | None = None
        self._event_session: str | None = None

    @property
    def requires_rtvi_processor(self) -> bool:
        """Use in-process delivery without a separate RTVI control processor."""
        return False

    async def on_push_frame(self, data: FramePushed) -> None:
        """Preserve the originating turn while observing standard Pipecat frames."""
        self._event_turn = data.frame.metadata.get("turn")
        self._event_session = data.frame.metadata.get("session")
        if (
            isinstance(data.frame, InterruptionFrame)
            and data.direction == FrameDirection.DOWNSTREAM
            and data.frame.id not in self._frames_seen
        ):
            self._queued_aggregated_text_frames.clear()
            self._bot_transcription = ""
        await super().on_push_frame(data)

    async def _handle_aggregated_llm_text(self, frame: AggregatedTextFrame) -> None:
        if isinstance(frame, TTSTextFrame):
            # Native text acknowledgement follows playback drain, after the
            # matching BotStoppedSpeakingFrame may already have been observed.
            await self._send_aggregated_llm_text(frame)
        else:
            await super()._handle_aggregated_llm_text(frame)

    async def _send_aggregated_llm_text(self, frame: AggregatedTextFrame) -> None:
        origin = self._event_turn, self._event_session
        self._event_turn = frame.metadata.get("turn")
        self._event_session = frame.metadata.get("session")
        try:
            await super()._send_aggregated_llm_text(frame)
        finally:
            self._event_turn, self._event_session = origin

    async def send_rtvi_message(self, model: BaseModel, exclude_none: bool = True) -> None:
        """Send an unchanged RTVI message inside a native session envelope."""
        self.bridge.emit(
            {
                "type": "rtvi",
                "turn": self._event_turn,
                "session": self._event_session,
                "message": model.model_dump(exclude_none=exclude_none),
            }
        )
