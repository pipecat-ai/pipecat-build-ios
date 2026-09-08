"""Native provider selection through the real embedded Pipecat pipeline."""

import pytest
from pipecat.services.apple.phonon import PhononTTSService
from pipecat.services.apple.pocket_tts import PocketTTSService
from pipecat.services.apple.tts import AppleNativeTTSService
from pipecat.services.settings import TTSSettings
from pipecat.utils.text.simple_text_aggregator import SimpleTextAggregator
from test_bot import assistant_messages, reply, respond, session


@pytest.mark.parametrize(
    ("service_type", "provider", "model"),
    [
        (PocketTTSService, "pocket-tts", "pocket-tts"),
        (PhononTTSService, "phonon", "gradium-phonon"),
    ],
)
def test_named_provider_services_share_native_playback(service_type, provider, model):
    service = service_type(None, text_aggregator=SimpleTextAggregator())
    assert isinstance(service, AppleNativeTTSService)
    assert service.provider_id == provider
    assert service._settings.model == model
    with pytest.raises(ValueError, match="model"):
        service_type(
            None,
            text_aggregator=SimpleTextAggregator(),
            settings=TTSSettings(model="another-provider"),
        )


async def test_older_hosts_default_to_explicit_pocket_tts_requests():
    async with session() as (bridge, host):
        generation = await host.say(bridge, "Hello")
        speech = await reply(bridge, host, generation, "Hello there.")
        assert speech["provider"] == "pocket-tts"
        assert bridge.tts.strategy.active_service.provider_id == "pocket-tts"
        await respond(bridge, speech, done=True)
        await host.wait(lambda event: event.get("state") == "listening")


async def test_provider_switch_cancels_old_playback_and_preserves_played_context():
    async with session() as (bridge, host):
        generation = await host.say(bridge, "Tell me something")
        first = await reply(bridge, host, generation, "Already played. Never completed.")
        marker = len(host.events)
        await host.playback(bridge, first, speaking=True)
        await host.playback(bridge, first, speaking=False)
        await respond(bridge, first, done=True)
        second = await host.wait(lambda event: event.get("operation") == "speak", after=marker)
        await host.playback(bridge, second, speaking=True)
        old_turn = bridge.turn
        old_service = bridge.tts.strategy.active_service
        assert second["request"] in bridge.pending

        switched = []

        @bridge.tts.strategy.event_handler("on_service_switched")
        async def on_switched(strategy, service):
            switched.append((service.provider_id, second["request"] in bridge.pending))

        host.session = "phonon-session"
        marker = len(host.events)
        await bridge.receive({"type": "start", "session": host.session, "provider": "phonon"})
        assert bridge.tts.strategy.active_service.provider_id == "phonon"
        assert second["request"] not in bridge.pending
        assert old_service._context_turns == {}
        cancelled = await host.wait(
            lambda event: (
                event.get("type") == "cancel_request" and event.get("request") == second["request"]
            ),
            after=marker,
        )
        assert cancelled["turn"] == old_turn
        assert cancelled["session"] == "test"
        assert switched == [("phonon", False)]

        # The old acknowledgement cannot mark its interrupted sentence played.
        await respond(bridge, second, done=True)
        generation = await host.say(bridge, "Continue with Phonon")
        assert generation["history"] == [
            {"role": "user", "content": "Tell me something"},
            {"role": "assistant", "content": "Already played."},
        ]
        speech = await reply(bridge, host, generation, "Now Phonon is selected.")
        assert speech["provider"] == "phonon"
        assert speech["session"] == "phonon-session"
        await respond(bridge, speech, done=True)
        await host.wait(
            lambda event: event.get("state") == "listening" and event.get("turn") == speech["turn"],
            after=marker,
        )
        assert assistant_messages(bridge) == [
            {"role": "assistant", "content": "Already played."},
            {"role": "assistant", "content": "Now Phonon is selected."},
        ]

        # The same worker can select PocketTTS again for the next conversation.
        host.session = "pocket-session"
        await bridge.receive({"type": "start", "session": host.session, "provider": "pocket-tts"})
        generation = await host.say(bridge, "Back to PocketTTS")
        speech = await reply(bridge, host, generation, "PocketTTS again.")
        assert speech["provider"] == "pocket-tts"
        assert speech["session"] == "pocket-session"
        await respond(bridge, speech, done=True)
        await host.wait(
            lambda event: event.get("state") == "listening" and event.get("turn") == speech["turn"],
            after=marker,
        )
        assert not bridge.pending
        assert not bridge.operations
        assert all(not service._context_turns for service in bridge.tts.services)
    assert not bridge.pending
    assert not bridge.operations


async def test_unknown_native_provider_is_rejected_before_session_state_changes():
    async with session() as (bridge, host):
        old_service = bridge.tts.strategy.active_service
        with pytest.raises(ValueError, match="Unknown native TTS provider"):
            await bridge.receive({"type": "start", "session": "invalid", "provider": "unknown"})
        assert bridge.session == host.session
        assert bridge.active
        assert bridge.tts.strategy.active_service is old_service
