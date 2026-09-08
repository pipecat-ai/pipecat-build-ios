import subprocess
import sys
import tomllib
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = {
    "onnxruntime",
    "numpy",
    "numba",
    "resampy",
    "soxr",
    "soundfile",
    "Pillow",
    "loudness",
    "openai",
}


def test_mobile_metadata_omits_desktop_dependencies():
    project = tomllib.loads((ROOT / "pipecat/pyproject.toml").read_text())["project"]
    for platform in ["ios", "android"]:
        environment = default_environment() | {"sys_platform": platform}
        requirements = [Requirement(item) for item in project["dependencies"]]
        active = {r.name for r in requirements if not r.marker or r.marker.evaluate(environment)}
        assert not (active & DESKTOP)
        assert {"pydantic", "loguru", "websockets"} <= active
    desktop = default_environment() | {"sys_platform": "darwin"}
    active = {r.name for r in requirements if not r.marker or r.marker.evaluate(desktop)}
    assert DESKTOP <= active


def test_real_pipeline_completes_a_turn_without_desktop_modules():
    code = """
import importlib.abc, sys
class NoDesktop(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'numpy','numba','onnxruntime','soxr','soundfile','PIL','loudness','aiohttp','aiofiles','nltk','openai','audioop'}:
            raise RuntimeError('Desktop module imported: ' + fullname)
sys.meta_path.insert(0, NoDesktop())
sys.path.insert(0, 'src/python')
sys.path.insert(0, 'pipecat/src')
from pipecat.workers.runner import WorkerRunner
from mobile_app.bot import create_bot
from pipecat.services.apple.bridge import AppleNativeBridge
import asyncio, re

async def check_native_turn():
    events = asyncio.Queue()
    seen = []
    def emit(event):
        seen.append(event)
        events.put_nowait(event)
    async def wait(predicate):
        async with asyncio.timeout(5):
            while True:
                event = await events.get()
                assert event.get('type') != 'error', event
                if predicate(event):
                    return event
    def boundary(text):
        match = re.search(r'[.!?](?=\\s+\\S)', text)
        return match.end() if match else 0
    bridge = AppleNativeBridge(emit)
    worker = create_bot(bridge, sentence_boundary_matcher=boundary)
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    await runner.add_workers(worker)
    task = asyncio.create_task(runner.run())
    try:
        await asyncio.wait_for(bridge.ready.wait(), 5)
        await bridge.receive({'type': 'start', 'session': 'mobile-imports'})
        await bridge.receive({'type': 'vad', 'confidence': .95, 'volume': 1,
                              'time': .1, 'session': 'mobile-imports'})
        await bridge.receive({'type': 'transcription', 'text': 'Say',
                              'final': False, 'start': .1, 'end': .1,
                              'session': 'mobile-imports'})
        await wait(lambda e: e.get('message', {}).get('type') == 'user-started-speaking')
        for index in range(6):
            await bridge.receive({'type': 'vad', 'confidence': 0, 'volume': 1,
                                  'time': .2 + index * .1, 'session': 'mobile-imports'})
        endpoint = await wait(lambda e: e.get('operation') == 'finalize_asr')
        await bridge.receive({'type': 'transcription', 'text': 'Say hello',
                              'final': True, 'start': .1, 'end': .7,
                              'session': 'mobile-imports'})
        await bridge.receive({'type': 'result', 'request': endpoint['request'], 'done': True})
        generate = await wait(lambda e: e.get('operation') == 'generate')
        assert generate['prompt'] == 'Say hello'
        await bridge.receive({'type': 'result', 'request': generate['request'],
                              'delta': 'Hello there. Nice to meet you.'})
        await bridge.receive({'type': 'result', 'request': generate['request'], 'done': True})
        for sentence in ['Hello there.', 'Nice to meet you.']:
            speak = await wait(lambda e: e.get('operation') == 'speak')
            assert speak['text'] == sentence
            await bridge.receive({'type': 'playback', 'request': speak['request'],
                                  'speaking': True, 'session': 'mobile-imports'})
            await bridge.receive({'type': 'playback', 'request': speak['request'],
                                  'speaking': False, 'session': 'mobile-imports'})
            await bridge.receive({'type': 'result', 'request': speak['request'], 'done': True})
        await wait(lambda e: e.get('state') == 'listening')
        assert bridge.context.messages == [
            {'role': 'user', 'content': 'Say hello'},
            {'role': 'assistant', 'content': 'Hello there. Nice to meet you.'},
        ], bridge.context.messages
        types = {event.get('message', {}).get('type') for event in seen}
        assert {'user-started-speaking', 'user-stopped-speaking',
                'bot-started-speaking', 'bot-stopped-speaking'} <= types, types
    finally:
        await bridge.receive({'type': 'stop', 'session': 'mobile-imports'})
        await runner.cancel()
        await asyncio.wait_for(task, 5)

asyncio.run(check_native_turn())
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr
