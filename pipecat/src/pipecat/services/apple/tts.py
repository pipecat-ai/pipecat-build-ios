"""Host-selected native speech using Pipecat's TTS orchestration."""

import asyncio
from collections.abc import AsyncGenerator, Sequence
from typing import TYPE_CHECKING, Any, ClassVar

from pipecat.frames.frames import (
    AggregatedTextFrame,
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    FunctionCallCancelFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSAudioPlayedFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.apple.text import PlainSpeechTextFilter
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService
from pipecat.utils.text.base_text_aggregator import BaseTextAggregator
from pipecat.utils.text.base_text_filter import BaseTextFilter
from pipecat.utils.text.markdown_text_filter import MarkdownTextFilter

if TYPE_CHECKING:
    from pipecat.services.apple.bridge import AppleNativeBridge


class AppleNativeTTSService(TTSService):
    """Share native playback orchestration between Apple-hosted TTS providers.

    Concrete services identify the provider prepared by the native host. Model
    loading, voice selection, credentials, and PCM remain native. Each speak
    request completes only after its audio has finished playing.
    """

    Settings = TTSSettings
    provider_id: ClassVar[str | None] = None
    _native_model = "native-tts"

    def __init__(
        self,
        bridge: "AppleNativeBridge",
        *,
        text_aggregator: BaseTextAggregator,
        text_filters: Sequence[BaseTextFilter] | None = None,
        settings: TTSSettings | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the native TTS adapter.

        Args:
            bridge: Native request bridge; credentials stay in the host's Keychain.
            text_aggregator: Pipecat text aggregation using native sentence boundaries.
            text_filters: Spoken-sentence filters; defaults to plain speech cleanup.
            settings: Service settings; the model and voice are selected by the host.
            **kwargs: Additional TTSService configuration.
        """
        defaults = TTSSettings(model=self._native_model, voice=None, language=None)
        if settings is not None:
            self._validate_native_settings(settings)
            defaults.apply_update(settings)
        super().__init__(
            audio_playback_is_external=True,
            text_aggregator=text_aggregator,
            text_filters=list(text_filters)
            if text_filters is not None
            else [MarkdownTextFilter(), PlainSpeechTextFilter()],
            push_stop_frames=True,
            sample_rate=24000,
            settings=defaults,
            **kwargs,
        )
        self.bridge = bridge
        self._response_turn: str | None = None
        self._context_turns: dict[str, str] = {}

    @classmethod
    def _validate_native_settings(cls, settings: TTSSettings) -> None:
        unsupported = [
            key
            for key, value in settings.given_fields().items()
            if value is not None and not (key == "model" and value == cls._native_model)
        ]
        if unsupported:
            raise ValueError(
                f"Native TTS settings are selected by the host: {', '.join(unsupported)}"
            )

    async def _update_settings(self, delta: TTSSettings) -> dict[str, Any]:
        self._validate_native_settings(delta)
        return await super()._update_settings(delta)

    def create_context_id(self) -> str:
        """Bind a Pipecat audio context to the response that supplied its text."""
        context_id = super().create_context_id()
        if self._response_turn is not None:
            self._context_turns.setdefault(context_id, self._response_turn)
        return context_id

    async def on_audio_context_completed(self, context_id: str) -> None:
        """Release the response identity after its acknowledged output is drained."""
        self._context_turns.pop(context_id, None)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Reject stale response text before base TTS aggregation or native playback.

        Args:
            frame: Incoming Pipecat frame. Standalone TTSSpeakFrame requests must
                include their owning user turn in metadata["turn"].
            direction: Direction of travel through the pipeline.
        """
        if isinstance(
            frame, (FunctionCallInProgressFrame, FunctionCallResultFrame, FunctionCallCancelFrame)
        ):
            # Apple's current response awaits its tool result. Serializing these
            # behind that response's still-open audio context would deadlock.
            await FrameProcessor.process_frame(self, frame, direction)
            await self.push_frame(frame, direction)
            return
        if direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, (LLMFullResponseStartFrame, TTSSpeakFrame)):
                turn = frame.metadata.get("turn")
                if not turn or turn != self.bridge.turn or not self.bridge.active:
                    return
                self._response_turn = turn
            elif isinstance(frame, (LLMTextFrame, AggregatedTextFrame, LLMFullResponseEndFrame)):
                turn = frame.metadata.get("turn", self._response_turn)
                if (
                    not turn
                    or turn != self._response_turn
                    or turn != self.bridge.turn
                    or not self.bridge.active
                ):
                    return
        context_id = self._turn_context_id
        await super().process_frame(frame, direction)
        if isinstance(frame, (InterruptionFrame, CancelFrame, EndFrame)):
            self._response_turn = None
            self._context_turns.clear()
        elif isinstance(frame, LLMFullResponseEndFrame):
            self._response_turn = None
            if context_id and not self.audio_context_available(context_id):
                self._context_turns.pop(context_id, None)

    async def push_frame(
        self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        """Keep native response identities on aggregated and acknowledged output."""
        if turn := self._context_turns.get(getattr(frame, "context_id", None)):
            frame.metadata.setdefault("turn", turn)
        await super().push_frame(frame, direction)

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        """Await native playback and acknowledge only the original response's audio.

        Args:
            text: Sentence supplied by Pipecat's text aggregator.
            context_id: Pipecat audio context bound to the response's turn.

        Yields:
            A playback acknowledgement or a tagged native error frame.
        """
        turn = self._context_turns.get(context_id)
        if turn is None or turn != self.bridge.turn or not self.bridge.active:
            raise asyncio.CancelledError
        try:
            payload = {"text": text}
            if self.provider_id is not None:
                payload["provider"] = self.provider_id
            async for _ in self.bridge.stream("speak", turn, **payload):
                pass
            if self.bridge.turn != turn or not self.bridge.active:
                raise asyncio.CancelledError
            yield TTSAudioPlayedFrame(context_id=context_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.bridge.turn != turn or not self.bridge.active:
                raise asyncio.CancelledError from exc
            error = ErrorFrame(str(exc), exception=exc)
            error.metadata["turn"] = turn
            yield error
