"""Commit Pipecat TTS text frames after native audio has finished playing."""

from pipecat.frames.frames import Frame, LLMFullResponseEndFrame, TTSTextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from ..state import ConversationState


class PlaybackContextProcessor(FrameProcessor):
    def __init__(self, state: ConversationState):
        # Commit playback acknowledgments before the next native event can
        # interrupt the turn; no extra processor queue is needed for this work.
        super().__init__(enable_direct_mode=True)
        self.state = state

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        turn = frame.metadata.get("turn")
        if direction == FrameDirection.DOWNSTREAM and self.state.is_current(turn):
            if isinstance(frame, TTSTextFrame) and frame.append_to_context:
                self.state.turn.spoken.append(frame.text)
            elif isinstance(frame, LLMFullResponseEndFrame):
                self.state.finish_turn(turn)
        await self.push_frame(frame, direction)
