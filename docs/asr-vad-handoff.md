# ASR/VAD integration handoff

`bot.py` imports `PocketTTSService` from `pipecat.services.apple.pocket_tts` and
`PhononTTSService` from `pipecat.services.apple.phonon`. Both share the Apple/iOS
playback base `AppleNativeTTSService` in `pipecat.services.apple.tts`. A standard
Pipecat `ServiceSwitcher` selects the concrete service from `start.provider`.
The existing native settings UI supplies that selection before each conversation;
Swift prepares its matching synthesizer. See [native TTS integration](native-tts.md)
for the provider contract.

## Native TTS contract

- Keep `VoiceAudioEngine` shared by microphone capture and `PCMPlayer`. The audio
  session uses `.voiceChat` and voice processing for echo cancellation. A second
  playback engine would remove the intended echo-reference path.
- The current `PCMPlayer.enqueue` contract is mono Float32 PCM at 24,000 Hz.
  Convert other provider formats before enqueueing, or update the player and
  Python transport/service sample rates together.
- Keep the microphone running during generation and playback. Stop the player
  node when a reply is interrupted; stop the shared engine only when ending the
  conversation.
- A native `speak` request finishes only after `player.finish()` confirms playback
  has drained. Report `playback` start/stop callbacks with its request and session
  IDs. Pipecat uses these to emit standard bot speech messages; it uses the final
  request acknowledgement to commit only fully played sentences to context.
- Carry the concrete provider ID on every `speak` request and validate it against
  the native session's prepared provider. Provider and voice selection stay fixed
  until the next conversation; the UI selection is propagated on each start.
- Cancel the provider's inference stream when its Swift request task is cancelled.
  Keep the existing session/turn/request checks before enqueueing audio. Late
  callbacks from an interrupted request must not enqueue audio for a new reply.
- Preserve `finalize_asr`, native transcript/VAD callbacks, capture readiness and
  mute generation handling in `ConversationModel`. They are independent of the
  selected TTS provider.

## Pipecat composition

`src/python/mobile_app/bot.py` configures the usual pipeline:

```text
transport.input() → stt → context_aggregator.user() → llm
                  → tts → transport.output() → context_aggregator.assistant()
```

Apple Speech and Foundation Models services extend Pipecat's STT/LLM bases.
The concrete TTS services extend the shared `AppleNativeTTSService` base, which
extends `TTSService` within the Apple provider namespace. The pipeline's `tts`
stage is the standard `ServiceSwitcher` containing those concrete providers.
The universal context aggregators own turns and played-text history.
`SimpleTextAggregator` supplies sentence buffering using the native sentence
matcher. The former app-specific
processor implementations, `state.py`, and `native.py` were removed.

Apple's `SpeechDetector.results` currently supplies no activity events, as
[documented by Apple](https://developer.apple.com/documentation/speech/speechdetector/results)
and confirmed with a speech fixture. The binding instead feeds native
SoundAnalysis speech-confidence scores into `AppleVADAnalyzer`, which reuses
Pipecat's VAD debounce state machine. Its classifier uses 500 ms windows and
100 ms hops; the configured start/stop debounce is 100/600 ms. These are not
measurements of total interruption latency.

The SwiftUI speech display consumes standard RTVI user/bot start/stop messages,
including acoustic VAD messages. It shows current speaking states and recent
activity. Final ASR is drained before Pipecat closes the user turn. Ordered close
callbacks prevent an immediately following speech start from overtaking that stop.

## Validation on September 8, 2026

- 81 app and focused Pipecat tests passed, including a complete two-sentence turn
  with desktop dependencies blocked, spoken interruptions, played-only context,
  stale callbacks, mute/resume, and the endpoint/start ordering regression. This
  run included the new `AppleNativeTTSService` import. Log:
  `.build/apple-vad-final-tests.log`.
- Separate existing Pipecat regression runs passed: 76 TTS/turn-start tests and
  70 RTVI/worker/turn-stop tests.
- Native speech fixtures verified SoundAnalysis detection and complete
  transcription across two consecutive ASR sessions while preserving the audio
  timeline. Details are in `validation.md`.
- Simulator and signed iPhone builds passed before the final Python fixes and
  PocketTTS changes. Simulator launch reported `PIPECAT_PYTHON_READY`.
  These existing app bundles are not a validation of the in-progress PocketTTS
  integration and should be rebuilt before installation.
- The speech activity view was rendered and visually checked at iPhone width:
  `.build/speech-activity-fixture.png`. It contains synthetic UI events, not a
  recorded conversation.

Final packaging and device installation should use the completed PocketTTS
checkout. Real speakerphone echo rejection, headset routing, and interruption
latency still require a spoken test on a physical iPhone. Preserve signing team
`T2S6FJ6R3Q` when updating the Xcode project. Credentials remain entered in the app;
no credential resource should be reintroduced into builds.
