"""PocketTTS synthesis through the native Apple host."""

from pipecat.services.apple.tts import AppleNativeTTSService


class PocketTTSService(AppleNativeTTSService):
    """Request PocketTTS speech from the host's prepared Core ML provider.

    The native host owns model loading and voice selection. Every speech request
    identifies PocketTTS so the host can reject a mismatched provider.
    """

    provider_id = "pocket-tts"
    _native_model = "pocket-tts"
