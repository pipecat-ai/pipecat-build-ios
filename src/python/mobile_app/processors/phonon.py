"""Send aggregated text to native Phonon and acknowledge completed playback."""

import asyncio

from pipecat.frames.frames import (
    AggregatedTextFrame,
    ErrorFrame,
    Frame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from ..native import NativeRequests
from ..state import ConversationState, tag_frame


class PhononProcessor(FrameProcessor):
    def __init__(self, state: ConversationState, native: NativeRequests):
        super().__init__()
        self.state = state
        self.native = native

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if (
            direction != FrameDirection.DOWNSTREAM
            or not isinstance(frame, AggregatedTextFrame)
            or frame.skip_tts
        ):
            await self.push_frame(frame, direction)
            return
        turn = frame.metadata.get("turn")
        if not self.state.is_current(turn) or self.state.turn.failed or not frame.text.strip():
            return
        frame.append_to_context = False
        frame.will_be_spoken = True
        await self.push_frame(frame, direction)
        try:
            await self.push_frame(tag_frame(TTSStartedFrame(), turn))
            self.state.emit({"type": "state", "state": "speaking", "turn": turn})
            async for _ in self.native.stream("speak", turn, text=frame.text):
                pass
            if not self.state.is_current(turn):
                return
            # Swift acknowledges after .dataPlayedBack, so only audible text
            # reaches the context processor, including on interrupted turns.
            played = TTSTextFrame(frame.text, aggregated_by=frame.aggregated_by)
            played.append_to_context = True
            await self.push_frame(tag_frame(played, turn))
            await self.push_frame(tag_frame(TTSStoppedFrame(), turn))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.state.is_current(turn):
                self.state.turn.failed = True
            await self.push_error_frame(tag_frame(ErrorFrame(str(exc), exception=exc), turn))
