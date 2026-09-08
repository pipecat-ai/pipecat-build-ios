import hashlib
import http.client
import io
import json
import shutil
import urllib.error
from pathlib import Path

import build_native
import build_options
import fetch_pocket_tts as fetch
import generate_project
import pytest
import setup
from bundle_runtime import bundle_models

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def assets(tmp_path):
    manifest = fetch.load_manifest()
    manifest["files"] = [
        {
            "path": f"v2.1/english/constants_bin/{name}",
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        for name, data in [("tokenizer.model", b"tokenizer"), ("alba.safetensors", b"voice")]
    ]
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/pocket_tts_manifest.json").write_text(json.dumps(manifest))
    return tmp_path, manifest, [b"tokenizer", b"voice"]


def install(root, manifest, contents):
    destination = root / "models/pocket-tts"
    for entry, data in zip(manifest["files"], contents, strict=True):
        path = fetch.asset_path(destination, manifest, entry)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return destination


def test_fetch_uses_pinned_urls_repairs_corruption_and_reuses_verified_files(assets, monkeypatch):
    root, manifest, contents = assets
    destination = root / "models/pocket-tts"
    calls = []

    def open_url(request, **kwargs):
        calls.append(request.full_url)
        return io.BytesIO(contents[len(calls) - 1])

    monkeypatch.setattr(fetch.urllib.request, "urlopen", open_url)
    fetch.fetch_assets(destination, manifest)
    assert len(calls) == 2
    assert all(f"/resolve/{manifest['revision']}/" in url for url in calls)
    assert json.loads((destination / "manifest.json").read_text()) == manifest
    fetch.verify_assets(destination, manifest)
    fetch.fetch_assets(destination, manifest)
    assert len(calls) == 2
    path = fetch.asset_path(destination, manifest, manifest["files"][0])
    path.write_bytes(b"corrupted")
    calls.clear()
    fetch.fetch_assets(destination, manifest)
    assert len(calls) == 1
    fetch.verify_assets(destination, manifest)


@pytest.mark.parametrize("failure", ["truncated", "checksum", "server"])
def test_failed_download_retries_and_never_replaces_existing_file(assets, monkeypatch, failure):
    root, manifest, contents = assets
    path = root / "weights.bin"
    path.write_bytes(b"existing")
    calls = []

    class Interrupted(io.BytesIO):
        def read(self, size=-1):
            raise http.client.IncompleteRead(b"partial")

    def open_url(request, **kwargs):
        calls.append(request.full_url)
        if failure == "server":
            raise urllib.error.HTTPError(request.full_url, 503, "Unavailable", {}, None)
        return Interrupted() if failure == "truncated" else io.BytesIO(b"bad-data")

    monkeypatch.setattr(fetch.urllib.request, "urlopen", open_url)
    monkeypatch.setattr(fetch.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="Could not fetch"):
        fetch.fetch_file("https://huggingface.co/file", path, manifest["files"][0])
    assert len(calls) == 3
    assert path.read_bytes() == b"existing"
    assert not path.with_name("weights.bin.partial").exists()


def test_retry_can_recover_without_accepting_partial_content(assets, monkeypatch):
    root, manifest, contents = assets
    responses = iter([b"short", contents[0]])
    monkeypatch.setattr(
        fetch.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(next(responses))
    )
    monkeypatch.setattr(fetch.time, "sleep", lambda _: None)
    path = root / "weights.bin"
    fetch.fetch_file("https://huggingface.co/file", path, manifest["files"][0])
    assert path.read_bytes() == contents[0]


def test_missing_file_reports_fetch_command_without_network(assets):
    root, manifest, _ = assets
    with pytest.raises(RuntimeError, match="scripts/fetch_pocket_tts.py"):
        fetch.verify_assets(root, manifest)


@pytest.mark.parametrize("path", ["../outside", "/outside", "a/../../outside", "a//b"])
def test_manifest_rejects_escaping_paths(assets, path):
    root, manifest, _ = assets
    manifest["files"][0]["path"] = path
    source = root / "scripts/pocket_tts_manifest.json"
    source.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="asset path"):
        fetch.load_manifest(source)


def test_asset_symlink_cannot_escape_destination(assets):
    root, manifest, _ = assets
    destination = root / "models/pocket-tts"
    destination.mkdir(parents=True)
    (destination / "Models").symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes destination"):
        fetch.asset_path(destination, manifest, manifest["files"][0])


def test_pinned_manifest_matches_runtime_configuration():
    manifest = fetch.load_manifest()
    assert (manifest["language"], manifest["precision"], manifest["placement"]) == (
        "english",
        "int8",
        "gpu",
    )
    assert manifest["cache_subdirectory"] == "Models/pocket-tts"
    assert manifest["default_voice"] in manifest["voices"]
    files = {entry["path"] for entry in manifest["files"]}
    prefix = manifest["language_subdirectory"]
    assert all(
        f"{prefix}/constants_bin/{voice}.safetensors" in files for voice in manifest["voices"]
    )
    assert {path.split("/")[2] for path in files} == {
        "cond_prefill.mlmodelc",
        "flowlm_stepv2.mlmodelc",
        "flow_decoder_fused.mlmodelc",
        "mimi_decoder.mlmodelc",
        "constants_bin",
    }


def test_public_packaging_removes_private_and_unlisted_assets(assets):
    root, manifest, contents = assets
    source = install(root, manifest, contents)
    (source / "unlisted-weights.bin").write_bytes(b"unused")
    bundle = root / "App.app"
    (bundle / "phonon").mkdir(parents=True)
    (bundle / "phonon/private.bin").write_bytes(b"private")
    bundle_models(bundle, root=root)
    assert not (bundle / "phonon").exists()
    assert not (bundle / "pocket-tts/unlisted-weights.bin").exists()
    fetch.verify_assets(bundle / "pocket-tts", manifest)
    assert json.loads((bundle / "pocket-tts/manifest.json").read_text()) == manifest


def private_package(root):
    for name in [
        "Cargo.toml",
        "Cargo.lock",
        "src/lib.rs",
        "model/model.q8.gguf",
        "model/config.json",
        "model/tokenizer.model",
        *(f"voices/{voice}.safetensors" for voice in build_options.PHONON_VOICES),
    ]:
        path = root / "models/phonon" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"private fixture")


def test_local_opt_in_and_public_reset_use_one_configuration(assets):
    root, _, _ = assets
    assert not build_options.phonon_enabled(root, environ={})
    build_options.write_build_options(True, root)
    assert build_options.phonon_enabled(root, environ={})
    assert not build_options.phonon_enabled(root, environ={"PIPECAT_ENABLE_PHONON": "NO"})
    config = (root / build_options.LOCAL_CONFIG).read_text()
    assert config == "PIPECAT_ENABLE_PHONON = YES\n"
    build_options.write_build_options(False, root)
    assert not build_options.phonon_enabled(root, environ={})
    assert "-lphonon_ffi" not in (root / build_options.LOCAL_CONFIG).read_text()


def test_private_packaging_requires_complete_package(assets):
    root, manifest, contents = assets
    install(root, manifest, contents)
    bundle = root / "App.app"
    with pytest.raises(RuntimeError, match="models/phonon/"):
        bundle_models(bundle, root=root, enable_phonon=True)
    private_package(root)
    bundle_models(bundle, root=root, enable_phonon=True)
    assert (bundle / "phonon/voices/Marlowe.safetensors").exists()
    assert not (bundle / "phonon/Cargo.toml").exists()


@pytest.mark.parametrize("enabled", [False, True])
def test_native_build_only_compiles_enabled_private_dependency(tmp_path, monkeypatch, enabled):
    calls = []
    monkeypatch.setattr(build_native, "ROOT", tmp_path)
    monkeypatch.setattr(build_native, "run", lambda args, **kw: calls.append(args))
    monkeypatch.setattr(build_native.subprocess, "check_output", lambda *a, **kw: "/SDK\n")
    if enabled:
        private_package(tmp_path)
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts/ios-toolchain.cmake").write_text("toolchain")
    build_native.build("simulator", enable_phonon=enabled)
    manifests = [str(command[command.index("--manifest-path") + 1]) for command in calls]
    assert any("pydantic_core" in path for path in manifests)
    assert any("phonon-ffi" in path for path in manifests) == enabled


def test_project_generation_pins_dependency_and_keeps_private_settings_local(assets, monkeypatch):
    root, manifest, _ = assets
    (root / "src/ios/PipecatVoice").mkdir(parents=True)
    (root / "config").mkdir()
    shutil.copy2(ROOT / "config/Build.xcconfig", root / "config/Build.xcconfig")
    monkeypatch.setattr(generate_project, "ROOT", root)
    generate_project.main()
    objects = list(generate_project.objects.values())
    dependency = next(x for x in objects if x["isa"] == "XCRemoteSwiftPackageReference")
    assert dependency["requirement"] == {
        "kind": "revision",
        "revision": manifest["fluid_audio"]["revision"],
    }
    public = (root / "PipecatVoice.xcodeproj/project.pbxproj").read_text()
    build_options.write_build_options(True, root)
    generate_project.main()
    assert (root / "PipecatVoice.xcodeproj/project.pbxproj").read_text() == public
    assert '"-lphonon_ffi"' not in public
    target = [
        x
        for x in objects
        if x["isa"] == "XCBuildConfiguration" and "baseConfigurationReference" in x
    ]
    assert len(target) == 2
    assert all(
        "$(PIPECAT_PHONON_SWIFT_CONDITION)"
        in x["buildSettings"]["SWIFT_ACTIVE_COMPILATION_CONDITIONS"]
        for x in target
    )


@pytest.mark.parametrize("quoted", [False, True])
def test_project_regeneration_preserves_signing_team(assets, monkeypatch, quoted):
    root, _, _ = assets
    (root / "src/ios/PipecatVoice").mkdir(parents=True)
    project = root / "PipecatVoice.xcodeproj/project.pbxproj"
    project.parent.mkdir()
    project.write_text(
        '"DEVELOPMENT_TEAM" = "ABC1234567";' if quoted else "DEVELOPMENT_TEAM = ABC1234567;"
    )
    monkeypatch.setattr(generate_project, "ROOT", root)
    generate_project.main()
    targets = [
        obj["buildSettings"]
        for obj in generate_project.objects.values()
        if obj["isa"] == "XCBuildConfiguration" and "baseConfigurationReference" in obj
    ]
    assert len(targets) == 2
    assert all(settings["DEVELOPMENT_TEAM"] == "ABC1234567" for settings in targets)


def test_setup_fetches_models_first_and_defaults_to_public(assets, monkeypatch):
    root, _, _ = assets
    (root / "Vendor/Python.xcframework").mkdir(parents=True)
    (root / ".build/pydantic_core-2.46.5").mkdir(parents=True)
    calls = []
    monkeypatch.setattr(setup, "ROOT", root)
    monkeypatch.setattr(setup.sys, "argv", ["setup.py", "--platform", "simulator"])
    monkeypatch.setattr(setup, "run", lambda *args: calls.append(tuple(map(str, args))))
    monkeypatch.setattr(
        setup,
        "write_build_options",
        lambda enabled: build_options.write_build_options(enabled, root),
    )
    setup.main()
    assert calls[0][1].endswith("scripts/fetch_pocket_tts.py")
    native = next(
        call for call in calls if any(x.endswith("scripts/build_native.py") for x in call)
    )
    assert "--disable-phonon" in native
    assert not build_options.phonon_enabled(root, environ={})
