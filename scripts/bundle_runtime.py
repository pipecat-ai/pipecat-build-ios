#!/usr/bin/env python3
"""Xcode build phase: package Python extensions as signed Apple frameworks."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def copy_tree(source: Path, target: Path):
    shutil.copytree(
        source,
        target,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", "libpython*.dylib"),
    )


def main():
    bundle = Path(os.environ["TARGET_BUILD_DIR"]) / os.environ["WRAPPER_NAME"]
    # Remove the credential resource from older builds when reusing their output.
    # Keys are entered in the app and stored only in the device's Keychain.
    (bundle / "DevelopmentVoiceSettings.plist").unlink(missing_ok=True)
    simulator = os.environ["PLATFORM_NAME"] == "iphonesimulator"
    slice_name = "ios-arm64_x86_64-simulator" if simulator else "ios-arm64"
    rust_target = "aarch64-apple-ios-sim" if simulator else "aarch64-apple-ios"
    python = ROOT / "Vendor/Python.xcframework"
    required = [
        ROOT / ".build/python-packages",
        python,
        ROOT / f".build/rust/{rust_target}/release/lib_pydantic_core.dylib",
    ]
    for path in required:
        if not path.exists():
            raise SystemExit(f"Missing {path}. Run: uv run --no-sync python scripts/setup.py")
    for name in ["python", "python-app", "python-packages", "phonon"]:
        path = bundle / name
        if path.exists():
            shutil.rmtree(path)
    copy_tree(python / "lib", bundle / "python/lib")
    copy_tree(python / slice_name / "lib-arm64", bundle / "python/lib")
    copy_tree(ROOT / "src/python", bundle / "python-app")
    copy_tree(ROOT / ".build/python-packages", bundle / "python-packages")
    # Pick up Pipecat source edits on every Xcode build without including ONNX
    # models, media assets, CLI templates, or host extension modules.
    for source in (ROOT / "pipecat/src/pipecat").rglob("*.py"):
        if "__pycache__" in source.parts:
            continue
        target = (
            bundle / "python-packages/pipecat" / source.relative_to(ROOT / "pipecat/src/pipecat")
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    suffix = "iphonesimulator" if simulator else "iphoneos"
    core = bundle / f"python-packages/pydantic_core/_pydantic_core.cpython-313-{suffix}.so"
    shutil.copy2(required[-1], core)
    copy_tree(ROOT / "models/phonon/model", bundle / "phonon/model")
    copy_tree(ROOT / "models/phonon/voices", bundle / "phonon/voices")

    frameworks = bundle / "Frameworks"
    frameworks.mkdir(parents=True, exist_ok=True)
    for base in [bundle / "python/lib/python3.13/lib-dynload", bundle / "python-packages"]:
        for extension in base.rglob("*.so"):
            relative = extension.relative_to(base)
            name = ".".join([*relative.parts[:-1], relative.name.split(".")[0]])
            framework = frameworks / f"{name}.framework"
            framework.mkdir(exist_ok=True)
            executable = framework / name
            shutil.move(extension, executable)
            marker = extension.with_suffix(".fwork")
            marker.write_text(str(executable.relative_to(bundle)) + "\n")
            (framework / f"{name}.origin").write_text(str(marker.relative_to(bundle)) + "\n")
            info = {
                "CFBundleExecutable": name,
                "CFBundleIdentifier": f"ai.pipecat.python.{name.replace('_', '-')}",
                "CFBundleName": name,
                "CFBundlePackageType": "FMWK",
                "CFBundleShortVersionString": "1.0",
                "CFBundleVersion": "1",
                "MinimumOSVersion": "26.0",
                "CFBundleSupportedPlatforms": ["iPhoneSimulator" if simulator else "iPhoneOS"],
            }
            (framework / "Info.plist").write_bytes(plistlib.dumps(info))
            subprocess.run(
                ["install_name_tool", "-id", f"@rpath/{name}.framework/{name}", str(executable)],
                check=True,
            )
            identity = os.environ.get("EXPANDED_CODE_SIGN_IDENTITY") or "-"
            if simulator or os.environ.get("CODE_SIGNING_ALLOWED") != "NO":
                subprocess.run(
                    ["codesign", "--force", "--sign", identity, "--timestamp=none", str(framework)],
                    check=True,
                    stderr=subprocess.DEVNULL,
                )
    print("Bundled embedded Python, Pipecat mobile runtime, and Phonon assets")


if __name__ == "__main__":
    main()
