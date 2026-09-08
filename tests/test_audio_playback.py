"""Exercise native playback and metering with an offline AVAudioEngine."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    sys.platform != "darwin" or not shutil.which("swiftc"), reason="macOS Swift SDK required"
)
def test_played_audio_meter_and_cancellation(tmp_path):
    binary = tmp_path / "playback-checks"
    subprocess.run(
        [
            "swiftc",
            "-swift-version",
            "5",
            "-parse-as-library",
            "-module-cache-path",
            str(tmp_path / "ModuleCache"),
            str(ROOT / "src/ios/PipecatVoice/PCMPlayer.swift"),
            str(ROOT / "tests/native/PlaybackChecks.swift"),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    result = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=20)
    assert "Native playback checks passed" in result.stdout
