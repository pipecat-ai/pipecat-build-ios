"""A native iOS voice agent composed with standard Pipecat services and turns."""

from collections.abc import Callable

from pipecat.audio.vad.apple import AppleVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.service_switcher import ServiceSwitcher
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frameworks.rtvi import RTVIObserverParams
from pipecat.services.apple.bridge import AppleNativeBridge
from pipecat.services.apple.llm import AppleFoundationLLMService
from pipecat.services.apple.phonon import PhononTTSService
from pipecat.services.apple.pocket_tts import PocketTTSService
from pipecat.services.apple.stt import AppleSpeechSTTService
from pipecat.transports.apple import AppleRTVIObserver, AppleTransport
from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies
from pipecat.utils.text.simple_text_aggregator import SimpleTextAggregator

INSTRUCTIONS = (
    "You are Pipecat, a friendly voice assistant. Respond in English using one or two "
    "short sentences, at most 80 words. Use plain spoken language without markdown. "
    "Be useful and direct. Do not claim to perform actions or access live information. "
    "Apple Speech recognizes the user's speech, Apple Foundation Models generates replies, "
    "and the app's selected native speech engine speaks your answers. "
    "Pipecat coordinates the conversation on the device."
)


def create_bot(
    bridge: AppleNativeBridge, *, sentence_boundary_matcher: Callable[[str], int]
) -> PipelineWorker:
    """Configure the native providers and the standard Pipecat voice pipeline."""
    transport = AppleTransport(bridge)
    stt = AppleSpeechSTTService(
        bridge,
        vad_analyzer=AppleVADAnalyzer(
            params=VADParams(confidence=0.65, start_secs=0.1, stop_secs=0.6, min_volume=0.0)
        ),
    )
    llm = AppleFoundationLLMService(bridge, system_instruction=INSTRUCTIONS)
    tts = ServiceSwitcher(
        services=[
            service(
                bridge,
                text_aggregator=SimpleTextAggregator(
                    sentence_boundary_matcher=sentence_boundary_matcher, max_buffer_chars=420
                ),
            )
            for service in (PocketTTSService, PhononTTSService)
        ]
    )
    context = LLMContext()
    context_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=ExternalUserTurnStrategies(wait_for_transcript=False)
        ),
    )
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ]
    )
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=24000),
        observers=[
            AppleRTVIObserver(
                bridge,
                params=RTVIObserverParams(
                    sentence_boundary_matcher=sentence_boundary_matcher,
                    vad_user_speaking_enabled=True,
                ),
            )
        ],
        enable_rtvi=False,
        enable_turn_tracking=False,
        idle_timeout_secs=None,
    )
    bridge.bind(worker, transport, context, context_aggregator, tts=tts)
    return worker
