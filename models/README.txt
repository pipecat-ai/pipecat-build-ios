PocketTTS model files are downloaded by setup, not committed to Git.

Fetch independently:
  uv run --no-sync python scripts/fetch_pocket_tts.py

Verify without network access:
  uv run --no-sync python scripts/fetch_pocket_tts.py --verify-only

The pinned English int8 assets and stock voices are stored in models/pocket-tts/.
The tracked manifest is scripts/pocket_tts_manifest.json. Setup and Xcode builds
verify its required files; the app bundle receives only those assets.

Everything in models/ except this note is ignored by Git. Reuse verified files
by rerunning the fetch script after an interrupted download.

Source: https://huggingface.co/FluidInference/pocket-tts-coreml
PocketTTS by Kyutai; Core ML conversion by Fluid Inference.
See THIRD_PARTY_NOTICES.md for attribution.
