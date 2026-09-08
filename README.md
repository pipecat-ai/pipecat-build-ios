# Pipecat Voice for iOS

A native SwiftUI voice app with **Pipecat running in embedded Python on the iPhone**.
Apple Speech recognizes speech, Apple Foundation Models generates replies, and
PocketTTS provides local speech synthesis through FluidAudio. No Python server
is required.

The SwiftUI interface pairs a luminous bot aura with a microphone button that
meters user audio, turns red when muted, and shows a spinner while connecting.
The aura expands, brightens, and swirls with 20 ms windows of played bot audio;
quiet speech and pauses stay visibly quieter.
Chat bubbles distinguish live transcription, composing replies, and final text;
inline markers preserve interruptions. Native glass controls, light/dark
appearance, Dynamic Type, VoiceOver, and Reduce Motion are supported. Capture and
playback share a voice-processing audio engine for echo cancellation.

See [validation](docs/validation.md) for build and runtime checks and the
remaining acoustic tests on a physical iPhone.

## Setup

Requires macOS, Xcode 26+, `uv`, stable Rust with edition 2024 support, and the
modified `pipecat/` checkout. Run:

```sh
uv sync --locked --group native
uv run --no-sync python scripts/setup.py
```

The voice orb uses a bundled Metal shader. If Xcode reports a missing Metal
compiler, install Apple's component with `xcodebuild -downloadComponent MetalToolchain`.

Setup first fetches the pinned English PocketTTS model from Hugging Face
(approximately **454 MB**, including 26 stock voices). Downloads are verified
against SHA-256 checksums; rerunning reuses valid files and repairs incomplete
or corrupt downloads. Model files live in `models/pocket-tts/`, are **ignored by
Git**, and are copied into local app builds. No Hugging Face account is needed.

To fetch or check the models independently:

```sh
uv run --no-sync python scripts/fetch_pocket_tts.py
uv run --no-sync python scripts/fetch_pocket_tts.py --verify-only
```

Setup also prepares checksum-pinned Python 3.13.14 and pydantic-core 2.46.5,
builds native dependencies, stages the mobile Python packages, and generates the
Xcode project with a revision-pinned FluidAudio package. Use `--platform simulator`
or `--platform device` to prepare one target. Only Apple silicon simulators are
supported. First-time setup and Swift package resolution require network access.

Open `PipecatVoice.xcodeproj`, select **PipecatVoice**, and choose your signing team.
The app requires an Apple Intelligence-capable iPhone with **iOS 26 or later**.
Enable Apple Intelligence and allow its models to finish downloading. Apple
English speech assets may download on first use.

```sh
xcodebuild -project PipecatVoice.xcodeproj -scheme PipecatVoice \
  -sdk iphonesimulator -configuration Debug \
  -derivedDataPath .build/DerivedData build
```

Keep normal Xcode signing enabled for simulator runs. Do not copy native Python
extensions from a desktop virtual environment into the app: setup cross-compiles
the iOS extensions and packaging installs them as signed frameworks.

## Conversation behavior

Pause after speaking to submit a turn, or tap **Send now**. The microphone stays
active while the assistant thinks and speaks. Speaking over a reply or tapping
**Interrupt** interrupts it. Tap the green microphone to mute or the red
microphone to unmute. Muting stops capture; ending the conversation or
backgrounding the app cancels generation and playback and stops the audio session.

Apple SoundAnalysis supplies acoustic speech confidence from 0.5-second windows
at 0.1-second intervals. Pipecat applies confidence `0.65`, start duration `0.1`
seconds, and stop duration `0.6` seconds. These settings live in `bot.py` and
provide acoustic endpointing. Final ASR results are drained before the LLM starts;
capture and VAD continue during that handoff.

Audio stays native. The Python pipeline receives transcripts, VAD confidence,
playback events, and request results. Only fully played assistant sentences enter
conversation context; interrupted sentences are omitted. Recent context is bounded
to eight messages and 5,000 characters. Transcripts remain in app memory.

PocketTTS assets are bundled and verified before loading, with no runtime model
downloads. Apple ASR and LLM use device models.
Provider failures are surfaced without a cloud or system-TTS fallback. The app is
currently English-only. Speakerphone echo rejection and latency require testing
on a physical iPhone.

## Development

The bot configuration is in `src/python/mobile_app/bot.py`:

```text
transport.input() → stt → context_aggregator.user() → llm
                  → tts → transport.output() → context_aggregator.assistant()
```

The bot imports concrete Apple TTS services:

```python
from pipecat.services.apple.pocket_tts import PocketTTSService
from pipecat.services.apple.phonon import PhononTTSService
```

Both extend `AppleNativeTTSService` in `pipecat.services.apple.tts`, which shares
the Apple/iOS playback integration with Pipecat's `TTSService`. A standard
`ServiceSwitcher` selects the provider sent by the existing native settings UI
in `start.provider` before each conversation. Swift prepares that provider and
validates the provider tag on every `speak` request. The host keeps the selected
voice, model loading, credentials, and PCM native.

`AppleSpeechSTTService`, `AppleFoundationLLMService`, `AppleTransport`, and
`AppleVADAnalyzer` provide the remaining native integrations; standard Pipecat
services and aggregators manage turns and context. Mobile dependencies exclude
desktop DSP and ONNX packages.

```sh
uv run --no-sync pytest -q
uv run --no-sync ruff check src/python scripts tests
uv run --no-sync ruff format --check src/python scripts tests
```

See [architecture](docs/architecture.md), [native TTS integration](docs/native-tts.md),
and [validation](docs/validation.md). The official Pipecat artwork is documented
in [src/branding/README.md](src/branding/README.md).

PocketTTS is by [Kyutai](https://huggingface.co/kyutai/pocket-tts); the Core ML
conversion is by [Fluid Inference](https://huggingface.co/FluidInference/pocket-tts-coreml).
The pinned conversion is distributed under CC BY 4.0; retain this attribution in
redistributed builds. See [third-party notices](THIRD_PARTY_NOTICES.md).
