#
# Copyright (c) 2024-2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Provider settings and native response identity through standard Pipecat services."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from loguru import logger

from pipecat.frames.frames import (
    AggregatedTextFrame,
    ErrorFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    LLMUpdateSettingsFrame,
    TTSAudioPlayedFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIObserver
from pipecat.services.apple.bridge import AppleNativeBridge
from pipecat.services.apple.llm import AppleFoundationLLMService, AppleLLMAdapter
from pipecat.services.apple.tts import AppleNativeTTSService
from pipecat.services.settings import LLMSettings, TTSSettings
from pipecat.tests.utils import SleepFrame, run_test
from pipecat.utils.text.simple_text_aggregator import SimpleTextAggregator


class NativeHost(AppleNativeBridge):
    """A local host that records operations and acknowledges complete playback."""

    def __init__(self, *, change_turn_during_playback: bool = False):
        super().__init__(lambda event: None)
        self.active = True
        self.turn = "original-turn"
        self.requests: list[dict[str, Any]] = []
        self.change_turn_during_playback = change_turn_during_playback

    async def stream(self, operation: str, turn: str | None, **payload) -> AsyncIterator[dict]:
        self.requests.append({"operation": operation, "turn": turn, **payload})
        await asyncio.sleep(0.005)
        if operation == "generate":
            yield {"delta": "First sentence. Second sentence."}
        elif self.change_turn_during_playback:
            self.turn = "new-turn"


def native_tts(host: NativeHost) -> AppleNativeTTSService:
    return AppleNativeTTSService(
        host,
        text_aggregator=SimpleTextAggregator(
            sentence_boundary_matcher=lambda text: text.find(".") + 1
        ),
    )


@pytest.mark.asyncio
async def test_service_settings_update_the_native_system_instruction():
    host = NativeHost()
    service = AppleFoundationLLMService(
        host,
        system_instruction="Argument instruction",
        settings=LLMSettings(system_instruction="Settings instruction"),
    )
    assert service._settings.system_instruction == "Settings instruction"
    context = LLMContext([{"role": "user", "content": "Hello"}])
    await run_test(
        service,
        frames_to_send=[
            LLMUpdateSettingsFrame(delta=LLMSettings(system_instruction="Updated instruction")),
            LLMContextFrame(context),
            SleepFrame(sleep=0.03),
        ],
    )
    assert host.requests[0]["instructions"] == "Updated instruction"


@pytest.mark.asyncio
async def test_settings_frame_for_another_service_is_forwarded_once():
    host = NativeHost()
    service = AppleFoundationLLMService(host)
    other = AppleFoundationLLMService(host)
    settings = LLMUpdateSettingsFrame(delta=LLMSettings(system_instruction="Other"), service=other)
    down, _ = await run_test(service, frames_to_send=[settings, SleepFrame(sleep=0.01)])
    assert [frame for frame in down if isinstance(frame, LLMUpdateSettingsFrame)] == [settings]
    assert service._settings.system_instruction is None


def test_adapter_resolves_system_context_without_mutating_conversation():
    messages = [
        {"role": "system", "content": "Context instruction"},
        {"role": "user", "content": "Question"},
    ]
    context = LLMContext(messages.copy())
    with pytest.warns(DeprecationWarning, match="system prompt"):
        params = AppleLLMAdapter().get_llm_invocation_params(
            context, system_instruction="Service instruction"
        )
    assert params == {
        "prompt": "Question",
        "history": [],
        "instructions": "Service instruction",
        "tools": [],
    }
    assert context.messages == messages


def test_adapter_rejects_overlong_turns_instead_of_silently_truncating():
    context = LLMContext([{"role": "user", "content": "x" * 2001}])
    with pytest.raises(ValueError, match="2,000"):
        AppleLLMAdapter().get_llm_invocation_params(context)


def test_provider_settings_reject_options_the_native_host_cannot_apply():
    host = NativeHost()
    with pytest.raises(ValueError, match="temperature"):
        AppleFoundationLLMService(host, settings=LLMSettings(temperature=0.2))
    with pytest.raises(ValueError, match="voice"):
        AppleNativeTTSService(
            host,
            text_aggregator=SimpleTextAggregator(),
            settings=TTSSettings(voice="another-voice"),
        )


@pytest.mark.asyncio
async def test_native_response_identity_survives_tts_aggregation_and_playback():
    host = NativeHost()
    service = native_tts(host)
    down, up = await run_test(
        Pipeline([AppleFoundationLLMService(host), service]),
        frames_to_send=[
            LLMContextFrame(LLMContext([{"role": "user", "content": "Hello"}])),
            SleepFrame(sleep=0.05),
        ],
    )
    assert not [frame for frame in up if isinstance(frame, ErrorFrame)]
    assert [request["turn"] for request in host.requests] == ["original-turn"] * 3
    speech = [
        frame
        for frame in down
        if isinstance(
            frame, (AggregatedTextFrame, TTSAudioPlayedFrame, TTSStartedFrame, TTSStoppedFrame)
        )
    ]
    assert len([frame for frame in speech if isinstance(frame, TTSTextFrame)]) == 2
    assert all(frame.metadata["turn"] == "original-turn" for frame in speech)
    assert service._context_turns == {}


@pytest.mark.asyncio
async def test_old_queued_sentences_never_use_a_new_native_turn():
    host = NativeHost(change_turn_during_playback=True)
    service = native_tts(host)
    frames = [
        LLMFullResponseStartFrame(),
        LLMTextFrame("Old first sentence. Old second sentence."),
        LLMFullResponseEndFrame(),
    ]
    for frame in frames:
        frame.metadata["turn"] = "original-turn"
    down, up = await run_test(
        service,
        frames_to_send=[
            *frames,
            SleepFrame(sleep=0.03),
            InterruptionFrame(),
            SleepFrame(sleep=0.01),
        ],
    )
    assert [request["turn"] for request in host.requests] == ["original-turn"]
    assert not [frame for frame in down if isinstance(frame, TTSTextFrame)]
    assert not [frame for frame in up if isinstance(frame, ErrorFrame)]
    assert service._context_turns == {}


@pytest.mark.parametrize("requires_processor", [True, False])
def test_rtvi_processor_validation_respects_observers_with_native_transport(requires_processor):
    class NativeObserver(RTVIObserver):
        @property
        def requires_rtvi_processor(self) -> bool:
            return requires_processor

    errors = []
    handler = logger.add(errors.append, level="ERROR")
    try:
        PipelineWorker(
            Pipeline([FrameProcessor()]), observers=[NativeObserver()], enable_rtvi=False
        )
    finally:
        logger.remove(handler)
    missing_processor = [error for error in errors if "no RTVIProcessor" in str(error)]
    assert bool(missing_processor) == requires_processor
