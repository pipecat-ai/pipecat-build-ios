"""Gradium Phonon synthesis through the native Apple host."""

from pipecat.services.apple.tts import AppleNativeTTSService


class PhononTTSService(AppleNativeTTSService):
    """Request Phonon speech from the host's prepared local provider.

    The native host owns model loading, voice selection, and credentials. Every
    speech request identifies Phonon so the host can reject a mismatched provider.
    """

    provider_id = "phonon"
    _native_model = "gradium-phonon"
