# Third-party notices

## PocketTTS Core ML assets

PocketTTS by Kyutai. Core ML conversion by Fluid Inference.

- Model: https://huggingface.co/FluidInference/pocket-tts-coreml
- Pinned revision: `f62fb9ef89b16fa206c5a57d958212c6c96f16ed`
- Upstream model: https://huggingface.co/kyutai/pocket-tts
- License declared by the pinned conversion's model card: [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/).

This app uses an unmodified subset of the English v2.1 conversion: the int8
FlowLM variant, conditioning and audio-decoder models, constants, tokenizer, and
stock voices. Other languages, model variants, and voice-cloning models are
omitted. Retain these credits and the license link when redistributing the assets
inside an app.

## FluidAudio

Copyright Fluid Inference and contributors. Apache License 2.0.

- Source: https://github.com/FluidInference/FluidAudio
- Pinned revision: `5c19d5e12320e22bbfb7a1877b089d2665a69add`
- License: [Apache 2.0 text](licenses/FluidAudio-LICENSE.txt), included in the app bundle.

The Swift package includes its own dependency and license metadata. Preserve
those notices alongside this file in distributed builds.
