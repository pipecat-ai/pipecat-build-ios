import asyncio
import re
from contextlib import asynccontextmanager

import pytest
from mobile_app.bot import create_bot
from pipecat.frames.frames import TranscriptionFrame
from pipecat.services.apple.bridge import AppleNativeBridge
from pipecat.services.apple.frames import AppleControlFrame, AppleInputFrame
from pipecat.services.apple.stt import AppleSpeechSTTService
from pipecat.workers.runner import WorkerRunner


class Host:
    """Native host with explicit ASR finalization and audio playback acknowledgments."""

    def __init__(self):
        self.events = []
        self.changed = asyncio.Event()
        self.time = 0.0
        self.session = "test"

    def emit(self, event):
        self.events.append(event)
        self.changed.set()

    async def wait(self, predicate, *, after=0, timeout=5):
        try:
            async with asyncio.timeout(timeout):
                while True:
                    match = next((event for event in self.events[after:] if predicate(event)), None)
                    if match:
                        return match
                    self.changed.clear()
                    await self.changed.wait()
        except TimeoutError:
            pytest.fail(f"Expected native event was not emitted. Events: {self.events[after:]}")

    async def vad(self, bridge, confidence, *, session=None):
        self.time = round(self.time + 0.1, 4)
        await bridge.receive(
            {
                "type": "vad",
                "confidence": confidence,
                "time": self.time,
                "volume": 1.0,
                "session": session or self.session,
            }
        )

    async def begin_speech(self, bridge):
        marker = len(self.events)
        await self.vad(bridge, 0.95)
        await self.wait(lambda event: rtvi_type(event) == "user-started-speaking", after=marker)
        return marker, self.time

    async def end_speech(self, bridge, text, *, start, after=0):
        for _ in range(6):
            await self.vad(bridge, 0.05)
        endpoint = await self.wait(
            lambda event: event.get("operation") == "finalize_asr", after=after
        )
        if text:
            await self.transcribe(bridge, text, start=start, final=True)
        endpoint_marker = len(self.events)
        await respond(bridge, endpoint, done=True)
        await self.wait(
            lambda event: rtvi_type(event) == "user-stopped-speaking", after=endpoint_marker
        )
        return endpoint

    async def transcribe(self, bridge, text, *, start, final, session=None):
        await bridge.receive(
            {
                "type": "transcription",
                "text": text,
                "final": final,
                "start": start,
                "end": self.time,
                "session": session or self.session,
            }
        )

    async def say(self, bridge, text):
        marker, start = await self.begin_speech(bridge)
        await self.end_speech(bridge, text, start=start, after=marker)
        return await self.wait(lambda event: event.get("operation") == "generate", after=marker)

    async def playback(self, bridge, request, *, speaking):
        await bridge.receive(
            {
                "type": "playback",
                "request": request["request"],
                "speaking": speaking,
                "session": self.session,
            }
        )


def rtvi_type(event):
    return event.get("message", {}).get("type") if event.get("type") == "rtvi" else None


@asynccontextmanager
async def session():
    host = Host()

    # The native tokenizer is verified separately; this host models its boundary API.
    def boundary(text):
        match = re.search(r"[.!?](?=\s+\S)", text)
        return match.end() if match else 0

    bridge = AppleNativeBridge(host.emit)
    worker = create_bot(bridge, sentence_boundary_matcher=boundary)
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    await runner.add_workers(worker)
    task = asyncio.create_task(runner.run())
    try:
        await asyncio.wait_for(bridge.ready.wait(), 5)
        await bridge.receive({"type": "start", "session": host.session})
        yield bridge, host
    finally:
        await bridge.receive({"type": "stop", "session": host.session})
        await runner.cancel()
        await asyncio.wait_for(task, 5)


async def respond(bridge, request, **fields):
    await bridge.receive({"type": "result", "request": request["request"], **fields})


async def reply(bridge, host, request, text):
    marker = len(host.events)
    await respond(bridge, request, delta=text)
    await respond(bridge, request, done=True)
    return await host.wait(lambda event: event.get("operation") == "speak", after=marker)


def assistant_messages(bridge):
    return [message for message in bridge.context.messages if message["role"] == "assistant"]


async def test_real_turn_emits_rtvi_speech_events_and_waits_for_playback():
    async with session() as (bridge, host):
        request = await host.say(bridge, "Hello")
        assert request["prompt"] == "Hello"
        speech = await reply(bridge, host, request, "Hello there.")
        assert speech["text"] == "Hello there."
        assert assistant_messages(bridge) == []
        assert not any(rtvi_type(event) == "bot-started-speaking" for event in host.events)
        await host.playback(bridge, speech, speaking=True)
        await host.wait(lambda event: rtvi_type(event) == "bot-started-speaking")
        assert assistant_messages(bridge) == []
        await host.playback(bridge, speech, speaking=False)
        await respond(bridge, speech, done=True)
        await host.wait(lambda event: event.get("state") == "listening")
        assert assistant_messages(bridge) == [{"role": "assistant", "content": "Hello there."}]
        await host.wait(lambda event: rtvi_type(event) == "bot-stopped-speaking")
        expected = [
            "user-started-speaking",
            "user-stopped-speaking",
            "bot-started-speaking",
            "bot-stopped-speaking",
        ]
        assert [
            rtvi_type(event) for event in host.events if rtvi_type(event) in expected
        ] == expected
        following = await host.say(bridge, "Another question")
        assert following["history"] == [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hello there."},
        ]
        speech_types = [rtvi_type(event) for event in host.events]
        assert (
            speech_types.index("user-started-speaking")
            < speech_types.index("user-stopped-speaking")
            < speech_types.index("bot-started-speaking")
            < speech_types.index("bot-stopped-speaking")
        )


async def test_spoken_barge_in_cancels_generation_and_discards_late_results():
    async with session() as (bridge, host):
        old = await host.say(bridge, "Old question")
        marker, start = await host.begin_speech(bridge)
        await host.wait(
            lambda event: (
                event.get("type") == "cancel_request" and event.get("request") == old["request"]
            ),
            after=marker,
        )
        await respond(bridge, old, delta="This must never be spoken.")
        await respond(bridge, old, done=True)
        await host.end_speech(bridge, "New question", start=start, after=marker)
        new = await host.wait(
            lambda event: (
                event.get("operation") == "generate" and event.get("request") != old["request"]
            ),
            after=marker,
        )
        assert new["prompt"] == "New question"
        assert new["turn"] != old["turn"]
        assert new["history"] == [{"role": "user", "content": "Old question"}]
        speech = await reply(bridge, host, new, "New answer.")
        assert speech["turn"] == new["turn"]
        assert speech["text"] == "New answer."
        assert all(
            "never be spoken" not in event.get("text", "")
            for event in host.events
            if event.get("operation") == "speak"
        )


async def test_spoken_barge_in_preserves_only_played_sentences_in_context():
    async with session() as (bridge, host):
        request = await host.say(bridge, "Explain")
        first = await reply(bridge, host, request, "First sentence. Second sentence.")
        assert first["text"] == "First sentence."
        await host.playback(bridge, first, speaking=True)
        await host.playback(bridge, first, speaking=False)
        await respond(bridge, first, done=True)
        second = await host.wait(
            lambda event: (
                event.get("operation") == "speak" and event.get("text") == "Second sentence."
            )
        )
        playback_marker = len(host.events)
        await host.playback(bridge, second, speaking=True)
        await host.wait(
            lambda event: rtvi_type(event) == "bot-started-speaking", after=playback_marker
        )
        marker, start = await host.begin_speech(bridge)
        await host.wait(
            lambda event: (
                event.get("type") == "cancel_request" and event.get("request") == second["request"]
            ),
            after=marker,
        )
        await host.wait(lambda event: rtvi_type(event) == "bot-stopped-speaking", after=marker)
        await respond(bridge, second, done=True)
        await host.playback(bridge, second, speaking=True)
        await host.end_speech(bridge, "Another question", start=start, after=marker)
        following = await host.wait(
            lambda event: event.get("operation") == "generate", after=marker
        )
        assert following["history"] == [
            {"role": "user", "content": "Explain"},
            {"role": "assistant", "content": "First sentence."},
        ]
        assert not any(rtvi_type(event) == "bot-started-speaking" for event in host.events[marker:])


async def test_tts_failure_reports_error_and_next_user_turn_can_recover():
    async with session() as (bridge, host):
        request = await host.say(bridge, "Hello")
        speech = await reply(bridge, host, request, "Hello.")
        await respond(bridge, speech, error="Invalid Gradium key")
        error = await host.wait(lambda event: event.get("type") == "error")
        assert "Invalid Gradium key" in error["message"]
        assert assistant_messages(bridge) == []
        following = await host.say(bridge, "Try again")
        assert following["prompt"] == "Try again"


async def test_empty_llm_response_reports_error_without_speech_or_assistant_context():
    async with session() as (bridge, host):
        request = await host.say(bridge, "Hello")
        await respond(bridge, request, done=True)
        error = await host.wait(lambda event: event.get("type") == "error")
        assert "empty response" in error["message"]
        assert assistant_messages(bridge) == []
        assert not any(event.get("operation") == "speak" for event in host.events)


async def test_vad_episode_without_transcript_does_not_trigger_generation():
    async with session() as (bridge, host):
        marker, start = await host.begin_speech(bridge)
        await host.end_speech(bridge, "", start=start, after=marker)
        # A following valid endpoint acts as an ordering barrier for the empty turn.
        request = await host.say(bridge, "An actual question")
        assert request["prompt"] == "An actual question"
        assert [
            event["prompt"] for event in host.events if event.get("operation") == "generate"
        ] == ["An actual question"]
        assert request["history"] == []


async def test_manual_send_waits_for_final_asr_result_instead_of_using_partial_text():
    async with session() as (bridge, host):
        marker, start = await host.begin_speech(bridge)
        await host.transcribe(bridge, "Book a train to", start=start, final=False)
        await bridge.receive({"type": "end_turn", "session": host.session})
        endpoint = await host.wait(
            lambda event: event.get("operation") == "finalize_asr", after=marker
        )
        assert not any(event.get("operation") == "generate" for event in host.events)
        await host.transcribe(bridge, "Book a train to Paris", start=start, final=True)
        await respond(bridge, endpoint, done=True)
        request = await host.wait(lambda event: event.get("operation") == "generate", after=marker)
        assert request["prompt"] == "Book a train to Paris"


async def test_muted_and_previous_session_audio_cannot_create_user_turns():
    async with session() as (bridge, host):
        await bridge.receive({"type": "mute", "session": host.session})
        await host.vad(bridge, 0.95)
        await host.transcribe(bridge, "Muted words", start=0, final=True)
        await bridge.receive({"type": "end_turn", "session": host.session})
        await bridge.receive({"type": "unmute", "session": host.session})
        await host.vad(bridge, 0.95, session="previous")
        await host.transcribe(bridge, "Stale words", start=0, final=True, session="previous")
        request = await host.say(bridge, "Current speech")
        assert request["prompt"] == "Current speech"
        assert request["history"] == []
        assert len([event for event in host.events if event.get("type") == "user_turn"]) == 1


async def test_manual_send_ignores_speech_window_tail_until_vad_is_quiet():
    async with session() as (bridge, host):
        marker, start = await host.begin_speech(bridge)
        await bridge.receive({"type": "end_turn", "session": host.session})
        endpoint = await host.wait(
            lambda event: event.get("operation") == "finalize_asr", after=marker
        )
        for _ in range(3):
            await host.vad(bridge, 0.95)
        await host.transcribe(bridge, "Send this now", start=start, final=True)
        await respond(bridge, endpoint, done=True)
        request = await host.wait(lambda event: event.get("operation") == "generate", after=marker)
        assert request["prompt"] == "Send this now"
        assert not any(
            event.get("type") == "cancel_request" and event.get("request") == endpoint["request"]
            for event in host.events
        )
        for _ in range(6):
            await host.vad(bridge, 0.05)
        following = await host.say(bridge, "A new thought")
        assert following["prompt"] == "A new thought"
        assert following["turn"] != request["turn"]


async def test_new_vad_start_waits_for_the_previous_queued_endpoint_to_close():
    async with session() as (bridge, host):
        user = bridge.aggregators.user()

        def processors(parent):
            yield parent
            for child in parent.processors:
                yield from processors(child)

        stt = next(
            processor
            for processor in processors(bridge.worker.pipeline)
            if isinstance(processor, AppleSpeechSTTService)
        )
        transcript_received, endpoint_received, next_vad_received = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )

        @user.event_handler("on_after_process_frame")
        async def received_transcript(processor, frame):
            if isinstance(frame, TranscriptionFrame) and frame.text == "First question":
                transcript_received.set()

        @stt.event_handler("on_before_process_frame")
        async def received_native_event(processor, frame):
            if isinstance(frame, AppleInputFrame) and frame.event.get("type") == "endpoint":
                endpoint_received.set()
            if (
                isinstance(frame, AppleControlFrame)
                and frame.event.get("type") == "vad"
                and frame.event.get("confidence", 0) > 0.9
                and endpoint_received.is_set()
            ):
                next_vad_received.set()

        marker, start = await host.begin_speech(bridge)
        await host.transcribe(bridge, "First question", start=start, final=True)
        await asyncio.wait_for(transcript_received.wait(), 5)
        # Hold the ordered turn-stop queue while system-priority VAD stays live.
        await user.pause_processing_frames()
        try:
            for _ in range(6):
                await host.vad(bridge, 0.05)
            endpoint = await host.wait(
                lambda event: event.get("operation") == "finalize_asr", after=marker
            )
            await respond(bridge, endpoint, done=True)
            await asyncio.wait_for(endpoint_received.wait(), 5)
            next_marker = len(host.events)
            await host.vad(bridge, 0.95)
            next_start = host.time
            await asyncio.wait_for(next_vad_received.wait(), 5)
        finally:
            await user.resume_processing_frames()
        await host.wait(
            lambda event: rtvi_type(event) == "user-started-speaking", after=next_marker
        )
        await host.end_speech(bridge, "Second question", start=next_start, after=next_marker)
        following = await host.wait(
            lambda event: (
                event.get("operation") == "generate" and event.get("prompt") == "Second question"
            ),
            after=next_marker,
        )
        assert following["history"] == [{"role": "user", "content": "First question"}]
        speech_types = [
            rtvi_type(event)
            for event in host.events
            if rtvi_type(event) in {"user-started-speaking", "user-stopped-speaking"}
        ]
        assert speech_types == [
            "user-started-speaking",
            "user-stopped-speaking",
            "user-started-speaking",
            "user-stopped-speaking",
        ]


async def test_muting_an_open_utterance_discards_its_already_finalized_segments():
    async with session() as (bridge, host):
        marker, start = await host.begin_speech(bridge)
        await host.transcribe(bridge, "Discard these words", start=start, final=True)
        await host.wait(lambda event: rtvi_type(event) == "user-transcription", after=marker)
        await bridge.receive({"type": "mute", "session": host.session})
        await bridge.receive({"type": "unmute", "session": host.session})
        following = await host.say(bridge, "Keep these words")
        assert following["prompt"] == "Keep these words"
        assert following["history"] == []
        assert [
            event["prompt"] for event in host.events if event.get("operation") == "generate"
        ] == ["Keep these words"]


async def test_muting_microphone_does_not_cancel_the_current_bot_response():
    async with session() as (bridge, host):
        request = await host.say(bridge, "Hello")
        speech = await reply(bridge, host, request, "Hello there.")
        await bridge.receive({"type": "mute", "session": host.session})
        await host.vad(bridge, 0.95)
        await host.transcribe(bridge, "Muted words", start=0, final=True)
        await host.playback(bridge, speech, speaking=True)
        await host.playback(bridge, speech, speaking=False)
        await respond(bridge, speech, done=True)
        await host.wait(lambda event: event.get("state") == "listening")
        assert assistant_messages(bridge) == [{"role": "assistant", "content": "Hello there."}]
        assert not any(
            event.get("type") == "cancel_request" and event.get("request") == speech["request"]
            for event in host.events
        )


async def test_final_asr_segments_are_combined_once_before_inference():
    async with session() as (bridge, host):
        marker, start = await host.begin_speech(bridge)
        await host.transcribe(bridge, "The first sentence.", start=start, final=True)
        await host.transcribe(bridge, "The first sentence.", start=start, final=True)
        host.time += 0.1
        await host.transcribe(bridge, "The second sentence.", start=host.time, final=True)
        await host.end_speech(bridge, "", start=start, after=marker)
        request = await host.wait(lambda event: event.get("operation") == "generate", after=marker)
        assert request["prompt"] == "The first sentence. The second sentence."
        assert request["history"] == []


async def test_restarting_session_cancels_old_generation_and_fences_old_audio():
    async with session() as (bridge, host):
        old = await host.say(bridge, "Before restart")
        old_session = host.session
        host.session = "new-session"
        marker = len(host.events)
        await bridge.receive({"type": "start", "session": host.session})
        await host.wait(
            lambda event: (
                event.get("type") == "cancel_request" and event.get("request") == old["request"]
            ),
            after=marker,
        )
        await respond(bridge, old, delta="Late answer.")
        await respond(bridge, old, done=True)
        await host.vad(bridge, 0.95, session=old_session)
        await host.transcribe(bridge, "Old audio", start=0, final=True, session=old_session)
        new = await host.say(bridge, "After restart")
        assert new["prompt"] == "After restart"
        assert new["session"] == host.session
        assert new["turn"] != old["turn"]
        assert (
            len([event for event in host.events[marker:] if event.get("type") == "user_turn"]) == 1
        )
        assert not any(event.get("operation") == "speak" for event in host.events[marker:])


async def test_transcript_queued_before_session_restart_cannot_enter_the_new_turn():
    async with session() as (bridge, host):
        await host.transcribe(bridge, "A queued old transcript", start=0, final=True)
        host.session = "restarted"
        await bridge.receive({"type": "start", "session": host.session})
        request = await host.say(bridge, "Only current speech")
        assert request["prompt"] == "Only current speech"
        assert request["history"] == []


async def test_transcript_queued_before_mute_cannot_enter_the_unmuted_turn():
    async with session() as (bridge, host):
        await host.transcribe(bridge, "A queued pre-mute transcript", start=0, final=True)
        await bridge.receive({"type": "mute", "session": host.session})
        await bridge.receive({"type": "unmute", "session": host.session})
        host.time = 0
        request = await host.say(bridge, "Only unmuted speech")
        assert request["prompt"] == "Only unmuted speech"
        assert request["history"] == []


async def test_vad_queued_before_mute_cannot_start_a_turn_after_unmute():
    async with session() as (bridge, host):
        await host.vad(bridge, 0.95)
        await bridge.receive({"type": "mute", "session": host.session})
        await bridge.receive({"type": "unmute", "session": host.session})
        host.time = 0
        request = await host.say(bridge, "An unmuted question")
        assert request["prompt"] == "An unmuted question"
        assert len([event for event in host.events if event.get("type") == "user_turn"]) == 1
        assert (
            len([event for event in host.events if rtvi_type(event) == "user-started-speaking"])
            == 1
        )


async def test_stopping_session_cancels_playback_and_ignores_late_completion():
    async with session() as (bridge, host):
        request = await host.say(bridge, "Hello")
        speech = await reply(bridge, host, request, "Hello there.")
        await host.playback(bridge, speech, speaking=True)
        await host.wait(lambda event: rtvi_type(event) == "bot-started-speaking")
        marker = len(host.events)
        await bridge.receive({"type": "stop", "session": host.session})
        await host.wait(
            lambda event: (
                event.get("type") == "cancel_request" and event.get("request") == speech["request"]
            ),
            after=marker,
        )
        await respond(bridge, speech, done=True)
        await host.playback(bridge, speech, speaking=True)
        assert assistant_messages(bridge) == []
        assert not any(rtvi_type(event) == "bot-started-speaking" for event in host.events[marker:])


@pytest.mark.parametrize("long_questions, retained_turns", [(False, 4), (True, 3)])
async def test_completed_conversations_keep_bounded_recent_turns(long_questions, retained_turns):
    async with session() as (bridge, host):
        completed = []
        for index in range(6):
            question = f"Question {index}: " + ("word " * 260 if long_questions else "Hello")
            question = question.strip()
            answer = f"Answer {index}."
            request = await host.say(bridge, question)
            speech = await reply(bridge, host, request, answer)
            await host.playback(bridge, speech, speaking=True)
            await host.playback(bridge, speech, speaking=False)
            await respond(bridge, speech, done=True)
            await host.wait(
                lambda event: (
                    event.get("state") == "listening" and event.get("turn") == request["turn"]
                )
            )
            completed.extend(
                [
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": answer},
                ]
            )
            assert len(bridge.context.messages) <= 8
            assert sum(len(message["content"]) for message in bridge.context.messages) <= 5000
        assert bridge.context.messages == completed[-retained_turns * 2 :]
        following = await host.say(bridge, "What next?")
        history = following["history"]
        assert 2 <= len(history) <= 8
        assert len(history) % 2 == 0
        assert sum(len(message["content"]) for message in history) <= 5000
        assert history == completed[-len(history) :]


async def test_cancelled_native_request_cleans_up_operation():
    events = []
    bridge = AppleNativeBridge(events.append)

    async def consume():
        async for _ in bridge.stream("generate", "one"):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    request = events[0]["request"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert bridge.pending == {}
    assert bridge.operations == {}
    assert events[-1] == {
        "type": "cancel_request",
        "request": request,
        "turn": "one",
        "session": None,
    }


async def test_native_request_timeout_reports_operation_and_cancels_it():
    events = []
    bridge = AppleNativeBridge(events.append, timeout=0.01)
    with pytest.raises(TimeoutError, match="Native speak timed out"):
        async for _ in bridge.stream("speak", "one"):
            pass
    assert bridge.pending == {}
    assert bridge.operations == {}
    assert events[-1]["type"] == "cancel_request"
