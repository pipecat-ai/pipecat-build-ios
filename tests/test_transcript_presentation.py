"""Exercise the actual Swift transcript projection without an audio runtime."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    sys.platform != "darwin" or not shutil.which("swiftc"), reason="macOS Swift SDK required"
)
def test_live_transcripts_and_interruption_events(tmp_path):
    binary = tmp_path / "transcript-checks"
    subprocess.run(
        [
            "swiftc",
            "-swift-version",
            "5",
            "-parse-as-library",
            "-module-cache-path",
            str(tmp_path / "ModuleCache"),
            str(ROOT / "src/ios/PipecatVoice/ConversationTranscript.swift"),
            str(ROOT / "tests/native/TranscriptChecks.swift"),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    result = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=10)
    assert "Native transcript checks passed" in result.stdout
