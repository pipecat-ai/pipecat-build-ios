# Validation

Validated on September 6, 2026 with Xcode 26.6 and the iOS 26.5 SDK/runtime.

| Check | Result |
| --- | --- |
| iPhone Release build, arm64, signing disabled | Passed |
| iPhone Debug build, arm64, development signing | Passed with the existing Xcode signing team |
| Signed app installation | Passed on the connected iPhone 16 Pro |
| Physical-device app launch and embedded pipeline | `PIPECAT_PYTHON_READY` observed on iPhone 16 Pro |
| Simulator Debug build, arm64 | Passed |
| Simulator app install and launch | Passed on iPhone 17 Pro / iOS 26.5 |
| Pipecat artwork | Header and voice indicator visually inspected; vector assets and light/dark 1024px icons compiled into both builds |
| Manual key entry builds | Debug iPhone and simulator builds passed; no legacy credential resource or automatic-key loader remains in either app |
| Fresh-install key entry | `PHONON_VOICE_KEY_MISSING` and `PIPECAT_PYTHON_READY` observed; UI shows **Set up voice** |
| Model ignore rules | Existing model assets, source, future model directories, and hidden files are ignored; `models/README.txt` is includable |
| Embedded CPython 3.13.14 starts real Pipecat pipeline | `PIPECAT_PYTHON_READY` observed |
| Phonon GGUF, SentencePiece tokenizer, Marlowe voice load | `PHONON_MODEL_LOADED` observed |
| Phonon key authentication | HTTP 200 from the supplied runtime's session endpoint |
| Native Phonon synthesis, iOS simulator arm64 library | 103,680 finite, non-silent samples; 4.32 seconds at 24 kHz |
| Mobile orchestration, native sentence boundaries, and dependency tests | 15 passed |
| Existing Pipecat audio utils, volume, pipeline, and LLM context tests | 71 passed |
| Existing Pipecat sentence, pattern-pair, skip-tag aggregation, and string tests | 72 passed |
| Python lint, formatting, byte compilation | Passed |
| Pipecat and app lockfile consistency | Passed; Pipecat package versions unchanged |

The branded simulator screen is `.build/pipecat-branded-screen.png`. Build logs
for that version are `.build/xcode-branding-simulator.log` and
`.build/xcode-branding-device.log`.
The subsequent builds with automatic key setup removed are recorded in
`.build/xcode-manual-key-simulator.log` and `.build/xcode-manual-key-device.log`.
The fresh-install screen is `.build/manual-key-setup-screen.png`.
The earlier core regression checks and the new text aggregation checks are
separate runs. The latter use desktop NLTK only in the test environment; the app
tests reject NLTK imports and exercise the actual native `NLTokenizer` bridge.

Normal Xcode simulator signing is required for the simulated application
identifier used by Keychain. A build with `CODE_SIGNING_ALLOWED=NO` could not save
keys; rebuilding with normal signing restored Keychain access.

The model-loading check uses an inert test credential only to validate local
assets. It does not call `TtsModel.run`, generate audio, or contact Gradium.

A separate authenticated synthesis check used the user-supplied key and the
existing iOS simulator Phonon static library through its C interface. It generated
"Hello! This is Phonon speaking from the bundled model." with Marlowe. The first
audio arrived after 0.221 seconds and synthesis finished in 1.546 seconds. These
are simulator measurements on the Mac, not iPhone performance measurements. The
output is `.build/phonon-key-test.wav`. That synthesis test passed the credential
in memory without logging it. Build-time key setup has since been removed: the
app reads keys only from its Keychain, and users enter them in Voice settings.
The local credential file and configuration script were deleted. The bundling
script removes the legacy credential resource from reused build output for all
configurations. The five tests for automatic development-key setup were removed
with that feature; the remaining 15 app tests pass.

A real spoken conversation, physical-device inference latency, audio routing,
microphone behavior, and Foundation Models responses still need testing on an
eligible physical device. The earlier signed app launched on the iPhone and its
embedded Python startup was verified. The latest build with manual key entry
installed successfully, but launching it was blocked because the phone was
locked. Its fresh-install setup and Python startup were verified in the simulator.
App Store distribution was not performed.
