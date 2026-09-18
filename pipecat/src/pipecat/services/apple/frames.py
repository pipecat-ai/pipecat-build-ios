"""Events from the native host; PCM stays in the host audio engine."""

from dataclasses import dataclass

from pipecat.frames.frames import DataFrame, SystemFrame, UninterruptibleFrame


@dataclass
class AppleInputFrame(DataFrame, UninterruptibleFrame):
    """An ordered native transcript or endpoint acknowledgment.

    Recognized speech can itself trigger a bot interruption. Keep that speech
    and its finalization acknowledgment ordered while STT checks their session
    and input epoch, rather than dropping them with cancelled bot output.

    Parameters:
        event: Native event payload.
    """

    event: dict


@dataclass
class AppleControlFrame(SystemFrame):
    """Native activity or lifecycle input that may interrupt a reply.

    Parameters:
        event: Native event payload.
    """

    event: dict
