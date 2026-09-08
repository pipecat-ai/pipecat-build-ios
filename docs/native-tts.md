# Native TTS integration

Pipeline code imports concrete Apple TTS providers:

```python
from pipecat.services.apple.pocket_tts import PocketTTSService
from pipecat.services.apple.phonon import PhononTTSService
```

Both are thin subclasses of `AppleNativeTTSService` in
`pipecat.services.apple.tts`. This shared Apple/iOS base extends Pipecat's
`TTSService` with `audio_playback_is_external=True` and coordinates native
playback with Pipecat context. `bot.py` uses a standard `ServiceSwitcher` to select
the concrete service from the native host's provider selection before each
conversation.

Swift's `PocketTTSSynthesizer` and optional `PhononSynthesizer` implement synthesis.
The existing settings UI chooses the provider and voice; Python receives the
provider ID but never credentials or PCM. Apple Speech and Foundation Models
retain their own services in `pipecat.services.apple`.

The Swift `SpeechSynthesizer` protocol provides `prepare(voice:)`,
`synthesize(_:)`, and `unload()`. Each `SpeechSynthesis` request owns its sample
stream, cancellation, and completion task. `ConversationModel` accepts a
provider factory, snapshots the selected provider and voice at startup, and
awaits teardown before starting another conversation. Settings are edited as a
draft and saved together; switching voices is disabled during a conversation.

## Bridge contract

The native `start` event carries `provider` (`pocket-tts` or `phonon`). The bridge
selects the matching service in Pipecat's `ServiceSwitcher` before processing
speech for that session. Each resulting `speak` request carries `provider`,
`request`, `session`, `turn`, and `text`. The native host rejects a provider tag
that differs from the synthesizer prepared for the session.

Selection remains fixed throughout a conversation. Provider and voice settings
are owned by the host; Python settings updates cannot change them. A saved UI
selection is propagated again when the next conversation starts.

The host streams mono Float32 samples at 24,000 Hz into the existing `PCMPlayer`
on the shared `VoiceAudioEngine`. It reports actual playback start/drain events
using the request identity and sends `result` with `done: true` only after the
whole sentence finishes playing. The service then yields
`TTSAudioPlayedFrame(context_id)`, allowing standard assistant aggregation to
commit the played text. Synthesis completion alone is not playback completion.

Cancellation stops that request's queued playback immediately, then cancels its
inference. It must keep microphone capture and VAD active and reject samples,
errors, and completions from old requests, sessions, and turns. Teardown waits
for inference to stop before the model is reused or released. Late cancellation
must not stop a newer request's player. The bridge retains its 90-second native
operation timeout.

## Bundled PocketTTS assets

`scripts/pocket_tts_manifest.json` pins both FluidAudio and the Hugging Face
snapshot. Setup downloads 48 files (454,245,751 bytes), all outside Git, and
packaging copies only that manifest's files. The bundle layout is:

```text
pocket-tts/
  manifest.json
  Models/pocket-tts/v2.1/english/
    cond_prefill.mlmodelc/
    flowlm_stepv2.mlmodelc/
    flow_decoder_fused.mlmodelc/
    mimi_decoder.mlmodelc/
    constants_bin/
```

The adapter passes the bundled `pocket-tts/` directory as FluidAudio's `directory`
argument. The pinned runtime appends `Models/pocket-tts/v2.1/english/` itself.
Configure `PocketTtsManager` with `.english`, `.int8`, and `.gpu`. Its defaults
use fp16 and would request a different model. Use the manifest's voice catalog,
with `alba` as the default; every listed voice has a bundled cache snapshot.

Validate the complete manifest before calling `initialize()` or loading a voice.
FluidAudio's loader downloads missing assets by default; validation must fail
locally before entering that path. The selected manifest includes
`bos_before_voice.bin`, avoiding its automatic backfill request. No cloning or
other language APIs are enabled. Core ML may keep its own writable compilation
caches outside the signed bundle.

Use a PocketTTS session per native request: `makeSession(voice:)`, enqueue the
sentence, finish input, and consume `frames`. Its `cancel()` awaits inference
termination. Keep playback completion and session completion separate. Providers
must not create a second player or reconfigure the shared audio session.

`PocketTTSSynthesizer` serializes inference requests and waits for the previous
session to finish before creating another. `PocketTTSAssets` checks every
bundled file's size and SHA-256 before initialization. Provider preferences
retain separate voices; unavailable saved providers resolve to PocketTTS.
The public default voice is Alba and requires no API key.

Debug builds accept `--verify-pocket-tts` to run synthesis, actual playback,
request cancellation, restart, and continuous-capture checks. This diagnostic
blocks HTTP(S) through `URLProtocol`, reports `POCKET_TTS_VERIFIED` on success,
and writes its first utterance to `Documents/pocket-tts-check.wav`. It exercises
the native adapter and shared audio engine independently of the LLM and ASR.

`config/Build.xcconfig` is the public build configuration. The project generator
references it and pins the FluidAudio package from the same model manifest.
Optional developer overrides live outside Git; regenerating the project does
not encode those overrides into the tracked project.
