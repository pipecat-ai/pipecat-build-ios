"""Share optional native feature settings between setup and Xcode builds."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_CONFIG = ".local/BuildOptions.xcconfig"
PHONON_VOICES = ("Marlowe", "Freya", "Archie", "Freddie", "Elodie-Rose", "Garrett", "Damon", "Zoey")


def phonon_enabled(root: Path = ROOT, *, environ: dict | None = None) -> bool:
    environment = os.environ if environ is None else environ
    value = environment.get("PIPECAT_ENABLE_PHONON")
    if value is None:
        path = root / LOCAL_CONFIG
        value = "NO"
        if path.exists():
            for line in path.read_text().splitlines():
                key, separator, setting = line.partition("=")
                if separator and key.strip() == "PIPECAT_ENABLE_PHONON":
                    value = setting.strip()
    if value not in {"YES", "NO"}:
        raise ValueError("PIPECAT_ENABLE_PHONON must be YES or NO")
    return value == "YES"


def write_build_options(enabled: bool, root: Path = ROOT) -> None:
    values = {
        "PIPECAT_ENABLE_PHONON": "YES" if enabled else "NO",
    }
    path = root / LOCAL_CONFIG
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".partial")
    temporary.write_text("".join(f"{key} = {value}\n" for key, value in values.items()))
    temporary.replace(path)


def validate_phonon(root: Path = ROOT) -> None:
    package = root / "models/phonon"
    required = [
        "Cargo.toml",
        "Cargo.lock",
        "src/lib.rs",
        "model/model.q8.gguf",
        "model/config.json",
        "model/tokenizer.model",
        *(f"voices/{voice}.safetensors" for voice in PHONON_VOICES),
    ]
    missing = [name for name in required if not (package / name).is_file()]
    if missing:
        raise RuntimeError(
            "Local Phonon build requires the complete private package in models/phonon/. "
            f"Missing: {', '.join(missing)}"
        )
