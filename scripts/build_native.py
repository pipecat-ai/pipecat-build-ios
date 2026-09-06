#!/usr/bin/env python3
"""Build Phonon and pydantic-core for Apple silicon iPhone/simulator targets."""

import argparse
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "device": ("aarch64-apple-ios", "iphoneos", "ios-arm64"),
    "simulator": ("aarch64-apple-ios-sim", "iphonesimulator", "ios-arm64_x86_64-simulator"),
}


def run(args, **kwargs):
    print("+", " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), check=True, **kwargs)


def build(platform):
    target, sdk, slice_name = TARGETS[platform]
    sdk_path = subprocess.check_output(
        ["xcrun", "--sdk", sdk, "--show-sdk-path"], text=True
    ).strip()
    env = os.environ.copy()
    env["PATH"] = str(ROOT / ".venv/bin") + os.pathsep + env["PATH"]
    env["CARGO_TARGET_DIR"] = str(ROOT / ".build/rust")
    env["SDKROOT"] = sdk_path
    env["IPHONEOS_DEPLOYMENT_TARGET"] = "26.0"
    env["CMAKE_SYSTEM_NAME"] = "iOS"
    env["CMAKE_OSX_SYSROOT"] = sdk_path
    env["CMAKE_OSX_ARCHITECTURES"] = "arm64"
    env["CMAKE_TOOLCHAIN_FILE"] = str(ROOT / "scripts/ios-toolchain.cmake")
    stamp = ROOT / f".build/sentencepiece-toolchain-{platform}.sha256"
    digest = hashlib.sha256((ROOT / "scripts/ios-toolchain.cmake").read_bytes()).hexdigest()
    if not stamp.exists() or stamp.read_text() != digest:
        for cached in (ROOT / f".build/rust/{target}/release/build").glob(
            "sentencepiece-sys-*/out/build"
        ):
            shutil.rmtree(cached)
        stamp.write_text(digest)
    run(
        [
            "cargo",
            "build",
            "--manifest-path",
            ROOT / "native/phonon-ffi/Cargo.toml",
            "--release",
            "--locked",
            "--target",
            target,
        ],
        env=env,
    )
    config = ROOT / f".build/pyo3-{platform}.txt"
    config.write_text(
        "\n".join(
            [
                "implementation=CPython",
                "version=3.13",
                "shared=true",
                "abi3=false",
                "pointer_width=64",
                "suppress_build_script_link_lines=true",
                "build_flags=",
            ]
        )
        + "\n"
    )
    framework = ROOT / "Vendor/Python.xcframework" / slice_name
    env["PYO3_CONFIG_FILE"] = str(config)
    env["PYO3_PYTHON"] = str(ROOT / ".venv/bin/python")
    # Link against the actual iOS Python framework, never the host libpython.
    env["RUSTFLAGS"] = (
        f"-L framework={framework} -l framework=Python -C link-arg=-Wl,-headerpad_max_install_names"
    )
    run(
        [
            "cargo",
            "build",
            "--manifest-path",
            ROOT / ".build/pydantic_core-2.46.5/Cargo.toml",
            "--release",
            "--locked",
            "--target",
            target,
        ],
        env=env,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=[*TARGETS, "all"], default="all")
    args = parser.parse_args()
    for platform in TARGETS if args.platform == "all" else [args.platform]:
        build(platform)
