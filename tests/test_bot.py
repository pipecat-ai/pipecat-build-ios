import asyncio
import re
from contextlib import asynccontextmanager

import pytest
from mobile_app.bot import VoiceAgent
from mobile_app.native import NativeRequests


class Host:
    def __init__(self):
        self.events = []
        self.changed = asyncio.Event()

    def emit(self, event):
        self.events.append(event)
        self.changed.set()

    async def wait(self, predicate, timeout=5):
        async with asyncio.timeout(timeout):
            while True:
                match = next((event for event in self.events if predicate(event)), None)
                if match:
                    return match
                self.changed.clear()
                await self.changed.wait()


@asynccontextmanager
async def session():
    host = Host()

    # The native tokenizer is verified separately; this host models its boundary API.
    def boundary(text):
        match = re.search(r"[.!?](?=\s+\S)", text)
        return match.end() if match else 0

    agent = VoiceAgent(host.emit, sentence_boundary_matcher=boundary)
    task = asyncio.create_task(agent.run())
    await asyncio.wait_for(agent.ready.wait(), 5)
    try:
        yield agent, host
    finally:
        await agent.close()
        await asyncio.wait_for(task, 5)


async def respond(agent, request, **fields):
    await agent.receive({"type": "result", "request": request["request"], **fields})


async def test_voice_turn_waits_for_playback_before_listening():
    async with session() as (agent, host):
        await agent.receive({"type": "transcription", "text": "Hello", "turn": "one"})
        request = await host.wait(lambda e: e.get("operation") == "generate")
        assert request["prompt"] == "Hello"
        await respond(agent, request, delta="Hello there. ")
        await respond(agent, request, done=True)
        speech = await host.wait(lambda e: e.get("operation") == "speak")
        assert speech["text"] == "Hello there."
        assert not any(e.get("state") == "listening" for e in host.events)
        assert agent.history == []
        await respond(agent, speech, done=True)
        await host.wait(lambda e: e.get("state") == "listening")
        assert agent.history == [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hello there."},
        ]


async def test_interruption_discards_late_generation_and_unplayed_history():
    async with session() as (agent, host):
        await agent.receive({"type": "transcription", "text": "Old question", "turn": "old"})
        old = await host.wait(lambda e: e.get("operation") == "generate")
        await agent.receive({"type": "interrupt"})
        await host.wait(
            lambda e: e.get("type") == "cancel_request" and e.get("request") == old["request"]
        )
        await respond(agent, old, delta="This must never be spoken. ")
        await respond(agent, old, done=True)
        await agent.receive({"type": "transcription", "text": "New question", "turn": "new"})
        new = await host.wait(lambda e: e.get("operation") == "generate" and e.get("turn") == "new")
        assert new["history"] == []
        await respond(agent, new, delta="New answer.")
        await respond(agent, new, done=True)
        speech = await host.wait(lambda e: e.get("operation") == "speak")
        assert speech["turn"] == "new"
        assert speech["text"] == "New answer."


async def test_tts_failure_recovers_for_another_turn():
    async with session() as (agent, host):
        await agent.receive({"type": "transcription", "text": "Hello", "turn": "one"})
        request = await host.wait(lambda e: e.get("operation") == "generate")
        await respond(agent, request, delta="Hello.")
        await respond(agent, request, done=True)
        speech = await host.wait(lambda e: e.get("operation") == "speak")
        await respond(agent, speech, error="Invalid Gradium key")
        error = await host.wait(lambda e: e.get("type") == "error")
        assert error["message"] == "Invalid Gradium key"
        assert agent.turn is None
        await agent.receive({"type": "transcription", "text": "Try again", "turn": "two"})
        await host.wait(lambda e: e.get("operation") == "generate" and e.get("turn") == "two")


async def test_cancelled_request_cleans_up_native_operation():
    events = []
    bridge = NativeRequests(events.append)

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
    assert events[-1] == {"type": "cancel_request", "request": request, "turn": "one"}


async def test_request_timeout_reports_the_operation_and_cancels_it():
    events = []
    bridge = NativeRequests(events.append, timeout=0.01)
    with pytest.raises(TimeoutError, match="Native speak timed out"):
        async for _ in bridge.stream("speak", "one"):
            pass
    assert bridge.pending == {}
    assert events[-1]["type"] == "cancel_request"


async def test_interrupting_speech_cancels_playback_without_committing_it():
    async with session() as (agent, host):
        await agent.receive({"type": "transcription", "text": "Hello", "turn": "one"})
        request = await host.wait(lambda e: e.get("operation") == "generate")
        await respond(agent, request, delta="Hello there.")
        await respond(agent, request, done=True)
        speech = await host.wait(lambda e: e.get("operation") == "speak")
        await agent.receive({"type": "stop"})
        await host.wait(
            lambda e: e.get("type") == "cancel_request" and e.get("request") == speech["request"]
        )
        await respond(agent, speech, done=True)
        assert agent.history == []
        assert agent.turn is None


async def test_interruption_preserves_only_completed_sentences_in_next_context():
    async with session() as (agent, host):
        await agent.receive({"type": "transcription", "text": "Explain", "turn": "one"})
        request = await host.wait(lambda e: e.get("operation") == "generate")
        await respond(agent, request, delta="First sentence. Second sentence.")
        await respond(agent, request, done=True)
        first = await host.wait(
            lambda e: e.get("operation") == "speak" and e.get("text") == "First sentence."
        )
        await respond(agent, first, done=True)
        second = await host.wait(
            lambda e: e.get("operation") == "speak" and e.get("text") == "Second sentence."
        )
        await agent.receive({"type": "interrupt"})
        await host.wait(
            lambda e: e.get("type") == "cancel_request" and e.get("request") == second["request"]
        )
        await respond(agent, second, done=True)
        await agent.receive({"type": "transcription", "text": "Another question", "turn": "two"})
        following = await host.wait(
            lambda e: e.get("operation") == "generate" and e.get("turn") == "two"
        )
        assert following["history"] == [
            {"role": "user", "content": "Explain"},
            {"role": "assistant", "content": "First sentence."},
        ]


async def test_empty_llm_response_reports_error_without_speech_or_context():
    async with session() as (agent, host):
        await agent.receive({"type": "transcription", "text": "Hello", "turn": "one"})
        request = await host.wait(lambda e: e.get("operation") == "generate")
        await respond(agent, request, done=True)
        error = await host.wait(lambda e: e.get("type") == "error")
        assert "empty response" in error["message"]
        assert agent.history == []
        assert agent.turn is None
        assert not any(e.get("operation") == "speak" for e in host.events)
