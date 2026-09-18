#
# Copyright (c) 2024-2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Native playback acknowledgements and cancellation through the real TTS base."""

import asyncio
import subprocess
import sys
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    ProposedUserStartedSpeakingFrame,
    ProposedUserStoppedSpeakingFrame,
    TTSAudioPlayedFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frameworks.rtvi import RTVIObserver, RTVIObserverParams
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService
from pipecat.tests.utils import SleepFrame, run_test
from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies
from pipecat.utils.text.simple_text_aggregator import SimpleTextAggregator


def sentence_boundary(text: str) -> int:
    """A deterministic sentence matcher supplied by a host runtime."""
    boundary = text.find(".")
    return boundary + 1 if boundary >= 0 else 0


class NativeTTS(TTSService):
    """Play each sentence before acknowledging it, without sending Python PCM."""

    def __init__(self, *, outcome="played", delay=0.04):
        super().__init__(
            audio_playback_is_external=True,
            push_stop_frames=True,
            stop_frame_timeout_s=0.005,
            text_aggregator=SimpleTextAggregator(sentence_boundary_matcher=sentence_boundary),
            settings=TTSSettings(model="native", voice=None, language=None),
        )
        self.outcome = outcome
        self.delay = delay
        self.requests = []
        self.cancelled = False

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        self.requests.append(text)
        try:
            await asyncio.sleep(self.delay)
            if self.outcome == "error":
                yield ErrorFrame("Native playback failed")
            elif self.outcome == "raise":
                raise RuntimeError("Native audio engine stopped")
            elif self.outcome == "wrong_context":
                yield TTSAudioPlayedFrame(context_id="another-request")
            elif self.outcome == "played":
                yield TTSAudioPlayedFrame(context_id=context_id)
            elif self.outcome == "error_after_ack":
                yield TTSAudioPlayedFrame(context_id=context_id)
                yield ErrorFrame("Native request failed after reporting playback")
        except asyncio.CancelledError:
            self.cancelled = True
            raise


@pytest.mark.asyncio
async def test_native_playback_waits_past_pcm_idle_timeout_and_preserves_text_order():
    service = NativeTTS()
    down, up = await run_test(
        service,
        frames_to_send=[
            LLMFullResponseStartFrame(),
            LLMTextFrame("First sentence. Second sentence."),
            LLMFullResponseEndFrame(),
            SleepFrame(sleep=0.15),
        ],
    )
    assert not [frame for frame in up if isinstance(frame, ErrorFrame)]
    assert service.requests == ["First sentence.", "Second sentence."]
    spoken = [frame for frame in down if isinstance(frame, TTSTextFrame)]
    assert [frame.text for frame in spoken] == service.requests
    assert sum(isinstance(frame, TTSStartedFrame) for frame in down) == 1
    assert sum(isinstance(frame, TTSStoppedFrame) for frame in down) == 1
    for index, frame in enumerate(down):
        if isinstance(frame, TTSTextFrame):
            assert isinstance(down[index - 1], TTSAudioPlayedFrame)
    assert down.index(spoken[-1]) < next(
        i for i, frame in enumerate(down) if isinstance(frame, LLMFullResponseEndFrame)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", ["missing", "error", "raise", "wrong_context", "error_after_ack"]
)
async def test_failed_native_playback_never_commits_unplayed_text(outcome):
    service = NativeTTS(outcome=outcome)
    down, up = await run_test(
        service,
        frames_to_send=[
            LLMFullResponseStartFrame(),
            LLMTextFrame("This sentence did not play."),
            LLMFullResponseEndFrame(),
            SleepFrame(sleep=0.08),
        ],
    )
    assert not [frame for frame in down if isinstance(frame, TTSTextFrame)]
    assert len([frame for frame in up if isinstance(frame, ErrorFrame)]) == 1
    assert [frame for frame in down if isinstance(frame, LLMFullResponseEndFrame)]


@pytest.mark.asyncio
async def test_interruption_cancels_native_playback_without_acknowledging_text():
    service = NativeTTS(delay=1)
    down, _ = await run_test(
        service,
        frames_to_send=[
            LLMFullResponseStartFrame(),
            LLMTextFrame("This sentence is interrupted."),
            LLMFullResponseEndFrame(),
            SleepFrame(sleep=0.03),
            InterruptionFrame(),
            SleepFrame(sleep=0.03),
        ],
    )
    assert service.cancelled
    assert not [frame for frame in down if isinstance(frame, (TTSAudioPlayedFrame, TTSTextFrame))]


@pytest.mark.asyncio
async def test_standard_assistant_context_keeps_only_played_sentences_on_interruption():
    class InterruptedNativeTTS(NativeTTS):
        async def run_tts(self, text, context_id):
            self.delay = 1 if self.requests else 0.01
            async for frame in super().run_tts(text, context_id):
                yield frame

    context = LLMContext()
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(user_turn_strategies=ExternalUserTurnStrategies()),
    )
    service = InterruptedNativeTTS()
    await run_test(
        Pipeline([service, aggregators.assistant()]),
        frames_to_send=[
            LLMFullResponseStartFrame(),
            LLMTextFrame("This sentence played. This sentence was interrupted."),
            LLMFullResponseEndFrame(),
            SleepFrame(sleep=0.05),
            InterruptionFrame(),
            SleepFrame(sleep=0.02),
        ],
    )
    assert service.cancelled
    assert context.messages == [{"role": "assistant", "content": "This sentence played."}]


@pytest.mark.asyncio
async def test_matching_sample_rate_does_not_load_resampler(monkeypatch):
    def unavailable():
        raise AssertionError("Resampling must not load for matching audio formats")

    monkeypatch.setattr("pipecat.services.tts_service.create_stream_resampler", unavailable)
    service = NativeTTS()
    service._sample_rate = 16000

    async def audio():
        yield b"\x00\x01" * 160

    frames = [
        frame
        async for frame in service._stream_audio_frames_from_iterator(audio(), in_sample_rate=16000)
    ]
    assert b"".join(frame.audio for frame in frames) == b"\x00\x01" * 160


@pytest.mark.asyncio
async def test_rtvi_uses_host_sentence_matcher(monkeypatch):
    def unavailable(text):
        raise AssertionError("Host sentence matching must not load NLTK")

    monkeypatch.setattr(
        "pipecat.processors.frameworks.rtvi.observer.match_endofsentence", unavailable
    )
    messages = []

    class NativeObserver(RTVIObserver):
        async def send_rtvi_message(self, model, exclude_none=True):
            messages.append(model)

    observer = NativeObserver(
        params=RTVIObserverParams(sentence_boundary_matcher=sentence_boundary)
    )
    await observer._handle_llm_text_frame(LLMTextFrame("A native sentence."))
    assert [message.type for message in messages] == ["bot-llm-text", "bot-transcription"]


@pytest.mark.asyncio
async def test_native_endpoint_can_end_a_turn_without_recognized_words():
    context = LLMContext()
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=ExternalUserTurnStrategies(wait_for_transcript=False)
        ),
    )
    down, _ = await run_test(
        aggregators.user(),
        frames_to_send=[
            ProposedUserStartedSpeakingFrame(),
            SleepFrame(sleep=0.02),
            ProposedUserStoppedSpeakingFrame(),
            SleepFrame(sleep=0.02),
        ],
    )
    assert [
        type(frame)
        for frame in down
        if isinstance(frame, (UserStartedSpeakingFrame, UserStoppedSpeakingFrame))
    ] == [UserStartedSpeakingFrame, UserStoppedSpeakingFrame]
    assert context.messages == []


def test_native_core_constructors_do_not_import_desktop_packages():
    source = Path(__file__).resolve().parents[1] / "src"
    script = """
import importlib.abc, sys
class NoDesktop(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'numpy', 'onnxruntime', 'soxr', 'PIL', 'loudness', 'nltk', 'openai'}:
            raise RuntimeError('Desktop dependency imported: ' + fullname)
sys.meta_path.insert(0, NoDesktop())
sys.path.insert(0, sys.argv[1])
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies
from pipecat.services.tts_service import TTSService
from pipecat.services.stt_service import STTService
from pipecat.services.llm_service import LLMService
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import TransportParams
from pipecat.processors.frameworks.rtvi import RTVIObserver, RTVIObserverParams
LLMContextAggregatorPair(LLMContext(), user_params=LLMUserAggregatorParams(user_turn_strategies=ExternalUserTurnStrategies()))
BaseInputTransport(TransportParams())
BaseOutputTransport(TransportParams())
class NativeTTS(TTSService):
    async def run_tts(self, text, context_id):
        yield None
NativeTTS(audio_playback_is_external=True)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(source)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
