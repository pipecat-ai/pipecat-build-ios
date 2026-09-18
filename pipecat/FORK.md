# Vendored Pipecat fork

This directory is a modified copy of [pipecat-ai/pipecat](https://github.com/pipecat-ai/pipecat),
tracked directly in this repository rather than as a submodule, so a plain clone builds.

- Upstream base: commit `1f8a513dd79c31f54bbd5b197fa26aefe242deca` (`v1.8.1-304-g1f8a513dd`, upstream `main`, September 2026).
- Package version: pinned statically in `pyproject.toml` as `1.8.2.dev304`, the version
  setuptools_scm derived from that base commit. There is no git metadata here, so
  setuptools_scm is not used.

## What differs from upstream

The changes are described by the fragments in `changelog/*.added`:

- `apple-services.added`: `pipecat.services.apple` (Speech STT, Foundation Models LLM,
  `AppleNativeTTSService` with `PocketTTSService` and `PhononTTSService`, native bridge,
  device tools), `pipecat.transports.apple`, and `pipecat.audio.vad.apple`.
- `mobile-runtime.added`: `sys_platform` markers so the core package installs on iOS/Android
  without desktop audio, vision, or ONNX dependencies; `mobile` dependency groups.
- `native-audio-playback.added`: TTS support for externally acknowledged native playback,
  custom text aggregators, and a configurable RTVI sentence matcher.
- `native-text-aggregation.added`: host-provided sentence boundaries and buffer limits in
  `SimpleTextAggregator`; frame metadata preserved through `LLMTextProcessor`.

Modified upstream files (beyond the new modules above): `pyproject.toml`, `uv.lock`,
`src/pipecat/audio/utils.py`, `audio/vad/vad_analyzer.py`, `frames/frames.py`,
`pipeline/worker.py`, `pipeline/worker_observer.py`, `processors/aggregators/llm_context.py`,
`processors/aggregators/llm_text_processor.py`, `processors/frameworks/rtvi/observer.py`,
`services/tts_service.py`, `transports/base_output.py`, `turns/user_start/__init__.py`,
`turns/user_turn_strategies.py`, `utils/text/simple_text_aggregator.py`.

## Updating from upstream

Diff this directory against the base commit in a fresh upstream clone to extract the patch,
rebase it onto the new upstream revision, copy the result back here, and bump the pinned
version and base commit above.
