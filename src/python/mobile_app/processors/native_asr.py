"""Convert a finalized Apple transcription into a Pipecat LLM context frame."""

from pipecat.frames.frames import Frame, LLMContextFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from ..state import ConversationState, tag_frame


class NativeASRInput(FrameProcessor):
    def __init__(self, state: ConversationState):
        super().__init__()
        self.state = state

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and direction == FrameDirection.DOWNSTREAM:
            turn = frame.metadata.get("turn")
            if not self.state.is_current(turn):
                return
            self.state.emit(
                {
                    "type": "transcript",
                    "role": "user",
                    "text": frame.text,
                    "turn": turn,
                    "final": True,
                }
            )
            await self.push_frame(frame, direction)
            await self.push_frame(tag_frame(LLMContextFrame(self.state.inference_context()), turn))
            return
        await self.push_frame(frame, direction)
