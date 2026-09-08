#!/usr/bin/env python3
"""Fetch the pinned English PocketTTS assets used by the native app."""

import argparse
import hashlib
import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts/pocket_tts_manifest.json"
DESTINATION = ROOT / "models/pocket-tts"


def relative_path(value: str) -> str:
    """Require a relative, normalized asset path before writing to disk."""
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or str(path) != value:
        raise ValueError(f"Invalid asset path: {value}")
    return value


def load_manifest(path: Path = MANIFEST) -> dict:
    manifest = json.loads(path.read_text())
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported PocketTTS manifest version")
    if not re.fullmatch(r"[\w-]+/[\w.-]+", manifest["repo_id"]):
        raise ValueError("Invalid Hugging Face repository")
    if not re.fullmatch(r"[0-9a-f]{40}", manifest["revision"]):
        raise ValueError("PocketTTS requires an immutable Hugging Face commit")
    relative_path(manifest["cache_subdirectory"])
    relative_path(manifest["language_subdirectory"])
    seen = set()
    for entry in manifest["files"]:
        path = relative_path(entry["path"])
        if path in seen or not path.startswith(manifest["language_subdirectory"] + "/"):
            raise ValueError(f"Unexpected or duplicate asset: {path}")
        seen.add(path)
        if type(entry["size"]) is not int or entry["size"] < 0:
            raise ValueError(f"Invalid asset size: {path}")
        if not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
            raise ValueError(f"Invalid asset checksum: {path}")
    if not seen:
        raise ValueError("PocketTTS manifest contains no assets")
    return manifest


def asset_path(destination: Path, manifest: dict, entry: dict) -> Path:
    path = destination / manifest["cache_subdirectory"] / entry["path"]
    if not path.resolve().is_relative_to(destination.resolve()):
        raise ValueError(f"Asset escapes destination: {entry['path']}")
    return path


def verified(path: Path, entry: dict) -> bool:
    if not path.is_file() or path.stat().st_size != entry["size"]:
        return False
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest() == entry["sha256"]


def verify_assets(destination: Path, manifest: dict) -> None:
    """Verify every required file without accessing the network."""
    for entry in manifest["files"]:
        if not verified(asset_path(destination, manifest, entry), entry):
            raise RuntimeError(
                f"Missing or invalid PocketTTS asset: {entry['path']}. "
                "Run: uv run --no-sync python scripts/fetch_pocket_tts.py"
            )


def fetch_file(url: str, path: Path, entry: dict, *, attempts: int = 3) -> None:
    """Install a file only after its complete contents match the pinned checksum."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    if partial.is_symlink():
        raise ValueError(f"Download temporary file is a symlink: {partial}")
    try:
        for attempt in range(attempts):
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "pipecat-ios-setup"})
                digest = hashlib.sha256()
                size = 0
                with (
                    urllib.request.urlopen(request, timeout=60) as response,
                    partial.open("wb") as f,
                ):
                    while block := response.read(1024 * 1024):
                        size += len(block)
                        if size > entry["size"]:
                            raise RuntimeError(f"Size mismatch: {entry['path']}")
                        digest.update(block)
                        f.write(block)
                if size != entry["size"] or digest.hexdigest() != entry["sha256"]:
                    raise RuntimeError(f"Checksum or size mismatch: {entry['path']}")
                partial.replace(path)
                return
            except (OSError, RuntimeError, http.client.HTTPException) as exc:
                if (
                    isinstance(exc, urllib.error.HTTPError)
                    and exc.code in {401, 403, 404}
                    or attempt == attempts - 1
                ):
                    raise RuntimeError(f"Could not fetch {entry['path']}: {exc}") from exc
                print(f"Retrying {entry['path']} ({attempt + 2}/{attempts})", flush=True)
                time.sleep(2**attempt)
    finally:
        partial.unlink(missing_ok=True)


def fetch_assets(destination: Path, manifest: dict) -> None:
    total = sum(entry["size"] for entry in manifest["files"])
    print(f"PocketTTS English int8: {total / 1_000_000:.1f} MB", flush=True)
    downloaded = 0
    for index, entry in enumerate(manifest["files"], 1):
        path = asset_path(destination, manifest, entry)
        if verified(path, entry):
            continue
        print(f"[{index}/{len(manifest['files'])}] {entry['path']}", flush=True)
        url = (
            f"https://huggingface.co/{manifest['repo_id']}/resolve/{manifest['revision']}/"
            + urllib.parse.quote(entry["path"], safe="/")
        )
        fetch_file(url, path, entry)
        downloaded += 1
    installed_manifest = destination / "manifest.json"
    temporary = installed_manifest.with_suffix(".partial")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n")
    temporary.replace(installed_manifest)
    print(
        f"PocketTTS ready: {downloaded} downloaded, {len(manifest['files']) - downloaded} reused."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify-only", action="store_true", help="Check local assets without network"
    )
    args = parser.parse_args()
    try:
        manifest = load_manifest()
        if args.verify_only:
            verify_assets(DESTINATION, manifest)
            print("PocketTTS assets verified.")
        else:
            fetch_assets(DESTINATION, manifest)
    except (OSError, ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
