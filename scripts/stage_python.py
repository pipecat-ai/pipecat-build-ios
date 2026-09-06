#!/usr/bin/env python3
"""Stage the mobile dependency closure using the resolved host versions.

Only pydantic-core needs a native extension. Its host binary is excluded here
and replaced with the iOS binary by bundle_runtime.py.
"""

import importlib.metadata
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DISTRIBUTIONS = [
    "annotated-types",
    "docstring-parser",
    "loguru",
    "pydantic",
    "pydantic-core",
    "typing-extensions",
    "typing-inspection",
    "websockets",
]


def main():
    destination = ROOT / ".build/python-packages"
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    manifest = {}
    for name in DISTRIBUTIONS:
        dist = importlib.metadata.distribution(name)
        manifest[name] = dist.version
        for file in dist.files or []:
            path = Path(str(file))
            if ".." in path.parts or path.suffix in {".so", ".dylib", ".pyc"}:
                continue
            source = Path(dist.locate_file(file))
            if source.is_file():
                target = destination / path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    if manifest["pydantic-core"] != "2.46.5":
        raise RuntimeError("Update the native pydantic-core build to match the locked dependency.")
    wheels = ROOT / ".build/wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(wheels),
            str(ROOT / "pipecat"),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    wheel = max(wheels.glob("pipecat_ai-*.whl"), key=lambda p: p.stat().st_mtime)
    with zipfile.ZipFile(wheel) as archive:
        for item in archive.infolist():
            path = Path(item.filename)
            if ".." in path.parts or path.is_absolute():
                raise ValueError("Invalid wheel path")
            # Only runtime code and distribution metadata belong in the app.
            if path.suffix in {".py", ".typed"} or any(
                p.endswith(".dist-info") for p in path.parts
            ):
                archive.extract(item, destination)
    (ROOT / ".build/mobile-package-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Staged {wheel.name} and {len(manifest)} mobile dependencies")


if __name__ == "__main__":
    main()
