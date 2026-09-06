"""Stream native Foundation Models results as Pipecat LLM response frames."""

import asyncio

from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from ..native import NativeRequests
from ..state import ConversationState, tag_frame


class FoundationModelProcessor(FrameProcessor):
    def __init__(self, state: ConversationState, native: NativeRequests, instructions: str):
        super().__init__()
        self.state = state
        self.native = native
        self.instructions = instructions

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM or not isinstance(frame, LLMContextFrame):
            await self.push_frame(frame, direction)
            return
        turn = frame.metadata.get("turn")
        if not self.state.is_current(turn):
            return
        text = ""
        messages = frame.context.messages
        try:
            await self.push_frame(tag_frame(LLMFullResponseStartFrame(), turn))
            async for event in self.native.stream(
                "generate",
                turn,
                prompt=messages[-1]["content"],
                instructions=self.instructions,
                history=messages[:-1],
            ):
                if not self.state.is_current(turn):
                    return
                delta = event.get("delta", "")
                text += delta
                self.state.emit(
                    {
                        "type": "transcript",
                        "role": "assistant",
                        "turn": turn,
                        "text": text,
                        "final": False,
                    }
                )
                await self.push_frame(tag_frame(LLMTextFrame(delta), turn))
            if not text.strip():
                raise RuntimeError("The language model returned an empty response. Try again.")
            self.state.emit(
                {
                    "type": "transcript",
                    "role": "assistant",
                    "turn": turn,
                    "text": text,
                    "final": True,
                }
            )
            await self.push_frame(tag_frame(LLMFullResponseEndFrame(), turn))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.state.is_current(turn):
                self.state.turn.failed = True
            await self.push_error_frame(tag_frame(ErrorFrame(str(exc), exception=exc), turn))
