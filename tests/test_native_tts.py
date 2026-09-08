"""Run the platform-independent Swift TTS contract checks on macOS."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    sys.platform != "darwin" or not shutil.which("swiftc"), reason="macOS Swift SDK required"
)
@pytest.mark.parametrize("enable_phonon", [False, True], ids=["public", "development"])
def test_swift_settings_assets_and_cancellation(tmp_path, enable_phonon):
    sources = ROOT / "src/ios/PipecatVoice"
    binary = tmp_path / "tts-checks"
    subprocess.run(
        [
            "swiftc",
            "-swift-version",
            "5",
            "-parse-as-library",
            *(["-D", "ENABLE_PHONON"] if enable_phonon else []),
            "-module-cache-path",
            str(tmp_path / "ModuleCache"),
            str(sources / "SpeechSynthesizer.swift"),
            str(sources / "PocketTTSAssets.swift"),
            str(sources / "VoiceSettings.swift"),
            str(ROOT / "tests/native/TTSChecks.swift"),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    result = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=10)
    assert "Native TTS checks passed" in result.stdout
