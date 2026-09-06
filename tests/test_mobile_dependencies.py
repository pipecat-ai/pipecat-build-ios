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


def test_real_pipeline_imports_without_desktop_modules():
    code = """
import importlib.abc, sys
class NoDesktop(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'numpy','numba','onnxruntime','soxr','soundfile','PIL','loudness','aiohttp','aiofiles','nltk','openai','audioop'}:
            raise RuntimeError('Desktop module imported: ' + fullname)
sys.meta_path.insert(0, NoDesktop())
sys.path.insert(0, 'src/python')
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.workers.runner import WorkerRunner
from pipecat.frames.frames import TranscriptionFrame
assert TranscriptionFrame('Hello', 'user', 'now').text == 'Hello'
from mobile_app.bot import VoiceAgent
import asyncio
async def check_aggregation():
    bot = VoiceAgent(lambda event: None, sentence_boundary_matcher=lambda text: 0)
    pieces = [part async for part in bot.text_aggregator.aggregate('A complete sentence. Next')]
    assert not pieces
    assert (await bot.text_aggregator.flush()).text == 'A complete sentence. Next'
asyncio.run(check_aggregation())
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
