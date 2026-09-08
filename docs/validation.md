# Validation

## September 8, 2026: speech-confirmed interruptions and audio routing

New turns now require native acoustic activity and a recognized ASR word.
Tests cover noise during LLM generation, bot playback, and sentence gaps;
one-word interruptions; early, stale, and delayed transcripts; resumed speech;
and stale endpoint acknowledgments. Native transcripts and endpoint acknowledgments
use Pipecat's `UninterruptibleFrame` semantics so the interruption they trigger
cannot discard the user's remaining words. Session and input-generation checks
still reject old events. The affected app/mobile checks (69 cases) and related
Pipecat bridge, playback, and turn checks (75 cases) passed.

The native finalization regression uses the actual `AppleSpeechRecognizer` with
file-backed capture and a deterministic pause after the first ASR input closes.
It cancels that endpoint's caller, retains the next 3.28-second utterance, then
requests a second endpoint before releasing the first rotation. The second
endpoint waits for its own final results, including the trailing words. A third
utterance also succeeds. Final timestamps span 0–3.2793125, 3.2793125–6.5593125,
and 6.5593125–9.8393125 seconds; all 231 capture buffers arrive and VAD continues.
The probe and generator are `.build/probe_native_finalization.swift` and
`.build/create_finalization_probe.py`. The local speech service required execution
outside the filesystem sandbox; the probe does not open a microphone.

`VoiceAudioEngine` now prefers verified native echo-cancelled input in default
mode and retains VoiceProcessingIO when unavailable. Session setup is shared by
the app and PocketTTS diagnostic. Route changes that remove AEC or change the
capture format stop the conversation with a restart message. DEBUG logs include
the chosen path, session mode, capability state, routes, and sample rate.
Both iPhone and Simulator Debug builds passed, and their bundled Apple services
match the final Python source. The Simulator diagnostic selected the
VoiceProcessingIO fallback and completed local PocketTTS load, playback,
cancellation, restart, and continuous capture. It produced 3.12 seconds of audio
in 9.24 seconds, confirming Simulator undersupply; this is not an iPhone timing
result. Logs are `.build/voice-audio-device-build.log`,
`.build/voice-audio-simulator-build.log`, and
`.build/voice-audio-simulator-runtime.log`.
The verified device build was installed successfully on the connected iPhone;
`.build/voice-audio-device-install.log` records installation. The phone remained
locked, so the app was not launched there for the acoustic/recording checks.

The actual PCM player passed native playback/cancellation checks. A streamed
six-chunk probe preserved all samples exactly; deliberately late chunks produced
the expected silence gaps. No playback buffering changes were made on the basis
of Simulator inference speed.

**Still requires an iPhone:** Control Center recording with its microphone off
and on, native-input AEC selection, speakerphone echo rejection, route changes,
and PocketTTS production/playback timing. The connected iPhone was locked during
these checks. The default-mode change is a candidate fix for recording compatibility,
not a verified Control Center recording result. Follow the recording steps in
the [README](../README.md#control-center-screen-recording).

## September 8, 2026: voice conversation interface

The conversation screen now uses independent user and bot audio levels. Native
capture drives the microphone bars, including during bot playback; playback
drives the orb. Mute clears the input meter, and cancellation/end clears playback
levels. The orb eases to rest after bot playback and pauses while the app is
inactive. Reduce Motion keeps it still.

The refined orb uses a bundled Metal shader with volumetric wisps and soft edges,
scheduled at up to 60 frames per second. Its analytic attack/release envelope
preserves the shape and clock across audio chunks, interruption, and resumed
playback. The Simulator app build and iPhone shader compilation passed. A local
motion probe checked continuous retargeting, eased attack/release, frozen idle,
and resuming without a phase jump. The three existing project generation tests
passed after adding Metal sources to the generator. GPU renders and Simulator
previews are in `.build/orb-review/`; device frame pacing has not been measured.

The audio-reactive refinement adds a flowing circular aura with gain-driven
expansion, ribbon separation, brightness, and motion speed. `PCMPlayer` now
schedules contiguous 20 ms windows and emits each level after playback, replacing
the average reported when an entire synthesis chunk was queued. A fixed decibel
range retains gain differences. The orb follows native playback activity directly
and uses a 70 ms attack / 260 ms release envelope.

The softer visual pass uses pearl blue and periwinkle throughout, with
translucent folds and tapered curls around the edge. Audio energy bends the
volume and its edge wisps together, and pulls the curls farther apart on louder
syllables. It keeps the same playback meter. Simulator builds, iPhone shader
compilation, and the motion continuity/release probe passed. Light/dark GPU
renders and a recorded-speech Simulator preview are in `.build/wisp-review/`.

`tests/test_audio_playback.py` exercises the actual player with an offline
AVAudioEngine: sample continuity (including a short final window), no premature
level on enqueue, quiet/loud/silent sections within one chunk, completion reset,
interruption, restart, and cancelled drain. Together with transcript and pipeline
regressions, 24 tests passed. Offline rendering uses `.dataRendered` because it has
no audio device; normal playback retains `.dataPlayedBack` and its output latency.
The Simulator preview in `.build/aura-review/aura-preview.mp4` uses the production
views and player with recorded PocketTTS speech at 0.25× and 1.5× gain. It delivered
328 meter updates with a median non-batched interval of approximately 21 ms; the
median nonzero level rose from 0.29 to 0.67. It uses a playback-only review engine,
so it does not validate microphone echo cancellation. iPhone shader compilation
and the motion continuity probe also passed.

Final user bubbles are blue, live user hypotheses are pale blue, composing bot
replies are violet, and final bot replies use an adaptive neutral surface. Live
bubbles have dashed outlines and text labels so color is not their only cue.
Amber, single-line markers distinguish user interruptions from conversations
ended during a reply, including interruption before the first generated token.
ASR hypotheses revise the current turn's bubble without changing committed text.

| Check | Result |
| --- | --- |
| Full Simulator Debug build with cached packages and normal signing | Passed; no Swift compiler warnings |
| Focused pipeline and native transcript tests | 23 passed; existing Pipecat deprecation warning only |
| New Python test lint and formatting | Passed |
| Actual app startup in iPhone 17 Pro Simulator, iOS 26.5 | Ready screen rendered successfully |
| Production SwiftUI views with local UI fixtures | Speaking, listening, connecting, muted, light/dark appearance, and accessibility-size layouts inspected |
| Interruption marker at accessibility text size 3 | Single line; adjacent divider lines yield space |
| Live transcript and controls | Latest bubble clears the dock; microphone has separate green/red states and a connecting spinner |
| Scrolling during streamed text updates | Reading position retained after scrolling back; Latest returns to the bottom |
| Accessibility tree | Speaker, live/final state, interruption, microphone state, copy action, and control labels exposed |

Screenshots and logs are in `.build/voice-ui-review/`. UI fixtures compile the
production views with an in-memory model and do not start audio or Python; they
verify layout and UI interaction, not acoustic behavior. The actual app was built
and launched separately. On an iPhone, verify that microphone input never moves
the bot orb, bot output never drives the microphone bars, and spoken interruption
cuts playback and leaves its inline event. Hardware audio, haptics, and VoiceOver
spoken navigation still need a device pass.

## September 8, 2026: native VAD and continuous ASR

The following native checks cover the new Apple audio/VAD binding. They used
Xcode 26.6 and the installed iOS/macOS 26.5 SDKs. Earlier application checks are
recorded separately below; they do not establish device behavior for spoken
interruptions.

| Check | Result |
| --- | --- |
| `VoiceAudioEngine`, `AppleSpeechRecognizer`, and `PCMPlayer` iOS simulator SDK typecheck | Passed with the project's Swift 5 and targeted concurrency settings; no warnings |
| Apple SpeechDetector paired with a progressive transcriber on a real speech fixture | Transcriber produced text; detector produced zero activity events |
| Apple SoundAnalysis `.version1` speech classification | Passed with 0.5-second windows and 0.1-second hops; speech confidence reached approximately 0.7–0.91 on the fixture |
| Actual native conversion, classification, and ASR handoff implementation | Two successive 4.32-second speech clips each produced both final sentences |
| Audio retained during ASR replacement | Passed; second clip's timestamps began at 4.3193125 seconds, preserving the continuous audio clock |
| Old ASR result stream drained before endpoint acknowledgment | Passed; 66 ms measured on this Mac for the fixture |

The native probes are `.build/probe_speech_detector.swift`,
`.build/probe_sound_analysis.swift`, and `.build/probe_native_handoff.swift`.
They use a locally generated speech fixture; no new synthesis was needed. Access to the host's speech model service
required running the speech probes outside the filesystem sandbox. The handoff
probe uses the actual `SpeechAudioInput` implementation from the application.

The SpeechDetector result matches Apple's
[documented empty stream](https://developer.apple.com/documentation/speech/speechdetector/results),
despite the SDK exposing `speechDetected`. The application uses SoundAnalysis
classification instead. Its 500 ms analysis window and Pipecat's stop debounce
affect responsiveness; the measured ASR drain time is only one part of endpoint
latency and is not an iPhone performance result.

Speakerphone echo rejection, noisy-room false starts, headset/Bluetooth routes,
and speech-to-interruption latency need testing on a physical iPhone. Useful
device checks are speaking over a long reply, pausing and resuming while ASR is
finalizing, muting during playback, and starting a new conversation immediately
after stopping. Confirm that the activity display records user and bot starts
and stops, that the next utterance retains its first and last words, and that an
interrupted assistant sentence is omitted from later context. Simulator startup
and deterministic Python host tests cannot establish these acoustic properties.

## PocketTTS setup and native host integration

The PocketTTS fetcher, checksum manifest, generic Python TTS service, Swift
adapters, provider settings, build gate, and model packaging are implemented.
The regenerated project preserves the existing signing team. The following
checks use the completed PocketTTS integration together with the VAD, continuous
ASR, interruption, and playback acknowledgment changes.

The model setup tests exercise pinned URLs, verified-file reuse, corruption
repair, interrupted transfers, checksum failure, retries, missing assets, public
and private asset selection, and reproducible project generation. Project tests
run in temporary directories so they do not modify the working client project.

| Check | Result |
| --- | --- |
| Complete pinned English int8 download | 48 files, 454,245,751 bytes; SHA-256 verification passed |
| Repeated fetch | 0 downloaded, all 48 files reused |
| Offline asset verification | Passed |
| Public model packaging with actual assets | Passed; all declared files verified in the output bundle |
| Root tests | 74 passed, including 21 model/setup cases and the compiled Swift contract checks |
| Pipecat Apple bridge, service and native-playback tests | 26 passed with repository-wide fixtures disabled (`--noconftest`) |
| Python lint and formatting | Passed for app/scripts/tests and the modified TTS service |
| Git model exclusions | Downloaded models and local build configuration remain ignored |
| Xcode build-setting expansion | Public mode omits private defines, include path, and library; local opt-in enables all three |
| Public simulator Debug build | Passed with normal signing; embedded Python reports `PIPECAT_PYTHON_READY` |
| Signed iPhone Debug build and installation | Passed; installed on the paired iPhone 16 Pro |
| iPhone Release build | Passed with code signing disabled for compilation/link verification |
| Optional local provider simulator build | Passed; switching back to the public build removes the private model assets |
| Public app bundle inspection | No private model directory or credential resource in either Debug build |
| Public binary symbols | No linked private TTS C functions |
| Swift contract checks | Default and migrated preferences, per-provider voice persistence, missing/corrupt/incomplete assets, cancellation and subsequent requests passed |
| Public settings screen | PocketTTS, Alba default, bundled voice catalog and model attribution visible; no key prompt |
| Simulator native PocketTTS diagnostic | Passed model initialization, finite/non-silent synthesis, actual playback drain, cancellation, restart, and continuous microphone capture; no HTTP(S) attempts |
| Generated speech through Apple ASR/VAD handoff | Both consecutive sessions transcribed the complete test utterance; the second began at 3.2793125 s, preserving the audio clock |

The Pipecat tests were run without repository-wide fixtures because the mobile
environment does not install `python-dotenv`, required by the desktop fixture
setup. Both Python runs report the existing `AudioContextTTSService` deprecation
warning. Swift contract checks compile the production settings, asset validator,
and request lifecycle code with the macOS SDK; model inference is tested in the
iOS diagnostic, rather than mocked in those checks.

The iPhone 17 Pro simulator (iOS 26.5) diagnostic measured 1.84 s for model
verification/loading and 0.80 s to first audio. It generated 3.28 s of audio in
9.18 s and drained cancelled inference in 0.19 s. Synthesis was slower than real
time in this simulator run, so this does not establish acceptable iPhone latency.
Core ML emitted simulator backend/cache warnings before loading successfully.
Apple Speech recovered “Hello there. Pocket TTS is speaking on this device.”
from the saved WAV in both consecutive ASR sessions, with a 58 ms first-session
drain. These measurements are single runs, not performance benchmarks.

Logs and the speech artifact are in `.build/pocket-simulator-build.log`,
`.build/pocket-device-build.log`, `.build/pocket-release-build.log`, `.build/private-simulator-build.log`,
`.build/pocket-simulator-runtime.log`, `.build/pocket-transcription-check.log`,
and `.build/pocket-simulator-check.wav`. Reproduce the native diagnostic by
launching a Debug build with `--verify-pocket-tts --verify-voice-settings`.

The signed app is installed on the iPhone, but the first diagnostic launch was
blocked by iOS because the device was locked. Hardware inference performance and
a complete spoken ASR → LLM → TTS conversation still require that launch and a
physical spoken test. Measure model loading, first audio, sustained synthesis,
memory, playback underruns, and VAD-triggered interruption latency. Test
speakerphone and Bluetooth routes; the deterministic native cancellation check
does not establish acoustic echo rejection or noisy-room behavior.
