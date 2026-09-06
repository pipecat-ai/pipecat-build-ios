"""Session context and native turn identity shared by the pipeline processors."""

from collections.abc import Callable
from dataclasses import dataclass, field

from pipecat.frames.frames import Frame
from pipecat.processors.aggregators.llm_context import LLMContext


def tag_frame(frame: Frame, turn: str) -> Frame:
    frame.metadata["turn"] = turn
    return frame


@dataclass
class Turn:
    id: str
    user: str
    spoken: list[str] = field(default_factory=list)
    # ErrorFrames travel upstream while response-end frames travel downstream.
    # Keep the turn alive until the worker has handled a pending failure.
    failed: bool = False


class ConversationState:
    """Retain complete played sentences within the on-device LLM's context budget."""

    def __init__(self, emit: Callable[[dict], None]):
        self.emit = emit
        self.context = LLMContext()
        self.turn: Turn | None = None

    def is_current(self, turn: str | None) -> bool:
        return self.turn is not None and self.turn.id == turn

    def inference_context(self) -> LLMContext:
        assert self.turn is not None
        return LLMContext([*self.context.messages, {"role": "user", "content": self.turn.user}])

    def commit(self) -> None:
        if self.turn and self.turn.spoken:
            self.context.add_messages(
                [
                    {"role": "user", "content": self.turn.user},
                    {"role": "assistant", "content": " ".join(self.turn.spoken)},
                ]
            )
            messages = self.context.messages[-8:]
            while messages and sum(len(message["content"]) for message in messages) > 5000:
                del messages[:2]
            self.context.set_messages(messages)
        self.turn = None

    def finish_turn(self, turn: str) -> None:
        if self.is_current(turn) and not self.turn.failed:
            self.commit()
            self.emit({"type": "state", "state": "listening", "turn": turn})
