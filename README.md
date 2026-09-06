# Pipecat Voice for iOS

A native SwiftUI voice app with **Pipecat running in embedded Python on the iPhone**.
Apple Speech handles ASR, Apple Foundation Models generates replies, and the supplied
Gradium Phonon model streams speech at 24 kHz. No Python server is required.

The interface includes live transcripts, microphone level animation, mute, manual
turn submission, tap-to-interrupt, end conversation, a voice picker, and Keychain
storage for the Phonon key. The official Pipecat wordmark and cat symbol appear in
the header, voice indicator, transcript avatars, and light/dark Home Screen icons.
Vector artwork and its source are documented in [src/branding/README.md](src/branding/README.md).

## Run the app

On this checkout, the generated project and build artifacts are prepared. Open
`PipecatVoice.xcodeproj`, select **PipecatVoice**, choose your signing team under
**Signing & Capabilities**, and run on an Apple Intelligence-capable iPhone with
**iOS 26 or later**. Enable Apple Intelligence in Settings and wait for its model
download to finish.

Tap **Set up voice** in the app and enter your Gradium **Phonon** key. It is stored
in the device's Keychain. Keys are not read from local build configuration or
included in any app build. The supplied runtime accepts `gsk_` followed by 64
lowercase hexadecimal characters. Use **Voice settings** to change or clear a
saved key; an existing installation may already have one in Keychain.

Tap **Let’s talk** and allow microphone access. English speech assets may download
on first use. Rebuild and run with **⌘R** to update an existing installation.

Pause for roughly a second to submit a turn, or tap **Send now**. Playback finishes
before listening resumes. **Tap to talk** interrupts a reply immediately. Muting
stops microphone capture; ending the conversation also cancels generation and
playback. The app stops its audio session when it enters the background.

## Build from a fresh checkout

Prerequisites: macOS, full Xcode 26+, the iOS SDK, an iOS 26 simulator for simulator
checks, `uv`, and stable Rust with edition 2024 support. Add the supplied Phonon
package to `models/phonon/`, following [models/README.txt](models/README.txt), and
keep the modified `pipecat/` checkout in place. Model assets and inference source
are supplied separately and ignored by Git.

```sh
uv sync --locked --group native
uv run --no-sync python scripts/setup.py
```

The setup script downloads checksum-pinned Python 3.13.14 and pydantic-core 2.46.5
sources, builds Rust libraries for iPhone and Apple silicon simulator, stages the
mobile Python dependencies, and generates the Xcode project. The first native
build takes several minutes. Use `--platform simulator` or `--platform device`
to prepare just one target. Intel simulators are not included.

```sh
# Compile for the simulator.
xcodebuild -project PipecatVoice.xcodeproj -scheme PipecatVoice \
  -sdk iphonesimulator -configuration Debug \
  -derivedDataPath .build/DerivedData build

# Validate a device release build; signing is configured separately in Xcode.
xcodebuild -project PipecatVoice.xcodeproj -scheme PipecatVoice \
  -sdk iphoneos -configuration Release \
  -derivedDataPath .build/DerivedData CODE_SIGNING_ALLOWED=NO build
```

Keep normal signing enabled for simulator runs: Xcode supplies the simulated
application identifier needed by Keychain. Disabling signing can prevent the
app from saving a key entered in Voice settings.

Do not replace the staged dependencies with packages from a macOS virtual
environment: desktop native extensions cannot load on iOS. The Xcode build phase
copies Python's standard library and transforms its extensions, plus the
cross-compiled pydantic-core extension, into separately signed frameworks with
`.fwork` import markers. It includes the supplied model, tokenizer, and voices and
omits Pipecat's desktop ONNX models and other unused media assets.

## Pipecat changes

The Python bot lives in `src/python/mobile_app/bot.py`. Native ASR, Foundation
Models, Phonon, and playback context processors each have their own file under
`src/python/mobile_app/processors/`. The bot uses Pipecat's `LLMTextProcessor`,
`SimpleTextAggregator`, `LLMContext`, and standard text, context, TTS, and error
frames. Apple's `NLTokenizer` supplies sentence boundaries through the native
bridge, and Pipecat handles buffering, flushing, and interruption resets.

`pipecat/pyproject.toml` has `mobile` and `mobile-dev` dependency groups and a
`mobile` extra. Platform markers omit desktop-only dependencies on `ios` and
`android`, while retaining the existing desktop requirements. Extras are additive:
an extra alone cannot subtract ONNX from mandatory dependencies, so the platform
markers do the actual dependency split.

Core imports defer NumPy, loudness, SOXR, audioop, Pillow, and DTMF dependencies
until those features are used. The worker observer uses the standard library
dataclass. `SimpleTextAggregator` accepts a native sentence matcher and an optional
buffer bound; `LLMTextProcessor` preserves source turn metadata. This app uses
native audio and explicit native turn boundaries, so it
does not instantiate Silero, Smart Turn, or a Python audio transport. Android
packaging and Python audio processing on mobile are not implemented here.

To install just the Pipecat mobile dependency group for desktop import checks:

```sh
cd pipecat
uv sync --only-group mobile
```

## Checks

```sh
uv run --no-sync pytest -q
uv run --no-sync ruff check src/python scripts tests
uv run --no-sync ruff format --check src/python scripts tests
```

The tests exercise a real Pipecat pipeline with a deterministic native host:
streaming, sentence aggregation, playback acknowledgments, interruption during
generation and playback, late callbacks, timeouts, and service errors. An import
guard also rejects any desktop dependency during core imports.

A Debug build accepts `--verify-model-load` as a launch argument. It loads the
bundled Phonon weights, tokenizer, and Marlowe voice and logs
`PHONON_MODEL_LOADED`. This diagnostic does not synthesize speech or call Gradium.
`PIPECAT_PYTHON_READY` confirms that the embedded interpreter started the real
pipeline. Simulator checks do not establish microphone quality, model latency, or
Foundation Models availability on a physical iPhone.

## Runtime and data behavior

The initial interaction is turn-based: microphone capture pauses during a reply
to avoid transcribing speaker output. Interruptions are explicit using **Tap to
talk**; automatic acoustic barge-in and streaming echo cancellation are not yet
provided. Silence-based turn submission is a 1.1-second transcript debounce, not
a semantic turn detector.

Apple ASR and LLM inference use the device. Phonon synthesizes audio locally, but
**the supplied Phonon runtime requires a Gradium key and sends session telemetry,
including synthesized text, to `phonon.gradium.ai`**. Its authentication and
telemetry behavior is preserved. This build therefore does not promise fully
offline or fully private operation. Review the supplied model's Gradium terms
before distributing it.

Transcripts live in app memory. The model context retains bounded pairs of user
messages and fully played assistant sentences. An interrupted sentence is omitted
from future context. Model failures are surfaced; there is no cloud or system-TTS
fallback. Language and phonon pronunciation are currently English-only.

Implementation notes are in [docs/architecture.md](docs/architecture.md).

References: [Apple SpeechAnalyzer](https://developer.apple.com/documentation/speech/speechanalyzer),
[Apple Foundation Models](https://developer.apple.com/documentation/foundationmodels),
[CPython on iOS](https://docs.python.org/3.13/using/ios.html),
[BeeWare Python support](https://github.com/beeware/Python-Apple-support), and the
[supplied Phonon documentation](models/phonon/README.md).
