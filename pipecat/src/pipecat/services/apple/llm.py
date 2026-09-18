"""Apple Foundation Models service for an embedded native host."""

import asyncio
import json
from dataclasses import replace
from typing import Any

from pipecat.adapters.base_llm_adapter import BaseLLMAdapter
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    FunctionCallCancelFrame,
    FunctionCallFromLLM,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    FunctionCallResultProperties,
    LLMConfigureOutputFrame,
    LLMContextFrame,
    LLMContextSummaryRequestFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    LLMUpdateSettingsFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext, LLMContextMessage
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.apple.bridge import AppleNativeBridge
from pipecat.services.apple.context import bounded_history
from pipecat.services.llm_service import LLMService
from pipecat.services.settings import LLMSettings


class AppleLLMAdapter(BaseLLMAdapter[dict[str, Any]]):
    """Adapt Pipecat's text context without importing a cloud provider SDK."""

    @property
    def id_for_llm_specific_messages(self) -> str:
        """Return the provider identifier used for native context messages."""
        return "apple"

    def get_llm_invocation_params(
        self, context: LLMContext, *, system_instruction: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """Build bounded text history and resolve the service's system instruction.

        Args:
            context: Pipecat conversation context.
            system_instruction: Composed instructions from service settings.
            **kwargs: Additional adapter arguments.

        Returns:
            Native prompt, history, and instructions without mutating the context.
        """
        messages, instructions = self._text_context(context, system_instruction)
        if not messages or messages[-1].get("role") != "user":
            raise ValueError("Apple Foundation Models requires a user message")
        prompt = messages[-1]["content"]
        if not prompt.strip() or len(prompt) > 2000:
            raise ValueError("Apple voice turns must contain between 1 and 2,000 text characters")
        return {
            "prompt": prompt,
            "history": bounded_history(messages[:-1]),
            "instructions": instructions,
            "tools": self.from_standard_tools(context.tools) or [],
        }

    def get_preparation_params(
        self, context: LLMContext, *, system_instruction: str | None = None
    ) -> dict[str, Any]:
        """Prepare committed history before a future user prompt is available.

        Args:
            context: Authoritative Pipecat context after playback or interruption.
            system_instruction: Composed instructions from service settings.

        Returns:
            Bounded history and instructions for native session prewarming.
        """
        messages, instructions = self._text_context(context, system_instruction)
        return {
            "history": bounded_history(messages),
            "instructions": instructions,
            "tools": self.from_standard_tools(context.tools) or [],
        }

    def _text_context(
        self, context: LLMContext, system_instruction: str | None
    ) -> tuple[list[dict[str, Any]], str]:
        messages = self.get_messages(context)
        system_from_context = self._extract_initial_system(
            messages, system_instruction=system_instruction
        )
        instructions = self._resolve_system_instruction(
            system_from_context, system_instruction, discard_context_system=True
        )
        pending: set[str] = set()
        for message in messages:
            role, content = message.get("role"), message.get("content")
            if role == "assistant" and message.get("tool_calls"):
                if content is not None and not isinstance(content, str):
                    raise ValueError("Native assistant tool calls require text or null content")
                for call in message["tool_calls"]:
                    function = call.get("function", {})
                    if (
                        call.get("type") != "function"
                        or not isinstance(call.get("id"), str)
                        or not isinstance(function.get("name"), str)
                        or not isinstance(function.get("arguments"), str)
                    ):
                        raise ValueError("Malformed native tool call history")
                    if not isinstance(json.loads(function["arguments"]), dict):
                        raise ValueError("Native tool arguments must be a JSON object")
                    pending.add(call["id"])
            elif role == "tool":
                call_id = message.get("tool_call_id")
                if call_id not in pending or not isinstance(content, str):
                    raise ValueError("Native tool results require a matching call and text content")
                if content == "IN_PROGRESS":
                    raise ValueError("Native context contains an unfinished tool call")
                pending.remove(call_id)
            elif role not in {"user", "assistant"} or not isinstance(content, str):
                raise ValueError(
                    "The native Apple host supports text and synchronous tool messages"
                )
        if pending:
            raise ValueError("Native context contains an unfinished tool call")
        return messages, instructions or ""

    def to_provider_tools_format(self, tools_schema: ToolsSchema) -> list[Any]:
        """Convert standard Pipecat tools into native dynamic tool declarations."""
        if tools_schema.custom_tools:
            raise NotImplementedError("Native Apple provider-specific tools are not supported")
        return [tool.to_default_dict() for tool in tools_schema.standard_tools]

    def get_messages_for_logging(self, context: LLMContext) -> list[LLMContextMessage]:
        """Return redacted context representations for provider diagnostics."""
        return self.get_messages(context, truncate_large_values=True)


class AppleFoundationLLMService(LLMService[AppleLLMAdapter]):
    """Stream on-device Foundation Models output using standard LLM frames."""

    adapter_class = AppleLLMAdapter
    Settings = LLMSettings

    def __init__(
        self,
        bridge: AppleNativeBridge,
        *,
        system_instruction: str | None = None,
        settings: LLMSettings | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the service.

        Args:
            bridge: Native request bridge.
            system_instruction: System prompt for Apple's language model.
            settings: Settings overrides; system_instruction is runtime-updatable.
            **kwargs: Additional LLMService configuration.
        """
        defaults = LLMSettings(
            model="apple-foundation-models",
            system_instruction=system_instruction,
            temperature=None,
            max_tokens=None,
            top_p=None,
            top_k=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
            filter_incomplete_user_turns=False,
            user_turn_completion_config=None,
        )
        if settings is not None:
            self._validate_native_settings(settings)
            defaults.apply_update(settings)
        super().__init__(settings=defaults, **kwargs)
        self.bridge = bridge
        self._native_calls: dict[str, dict[str, Any]] = {}
        self._compose_system_instruction()

    async def push_frame(
        self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        """Preserve native call identity and let Apple's session continue after results."""
        if isinstance(
            frame, (FunctionCallInProgressFrame, FunctionCallResultFrame, FunctionCallCancelFrame)
        ):
            call = self._native_calls.get(frame.tool_call_id)
            if call is not None:
                frame.metadata.update(session=call["session"], turn=call["turn"])
                if isinstance(frame, FunctionCallResultFrame):
                    frame.run_llm = False
                    frame.properties = replace(
                        frame.properties or FunctionCallResultProperties(), run_llm=False
                    )
                elif isinstance(frame, FunctionCallCancelFrame):
                    frame.run_llm = False
        await super().push_frame(frame, direction)

    def tool_result_committed(self, frame: Frame) -> None:
        """Resume a native tool only after the standard aggregator records its outcome.

        Args:
            frame: A downstream function result or cancellation already processed
                by the assistant context aggregator.
        """
        if not isinstance(frame, (FunctionCallResultFrame, FunctionCallCancelFrame)):
            return
        call = self._native_calls.pop(frame.tool_call_id, None)
        if (
            call is None
            or not self.bridge.active
            or call["session"] != self.bridge.session
            or call["turn"] != self.bridge.turn
            or call["request"] not in self.bridge.pending
        ):
            return
        self.bridge.emit(
            {
                "type": "tool_result",
                **call,
                "tool_call_id": frame.tool_call_id,
                "result": frame.result
                if isinstance(frame, FunctionCallResultFrame)
                else "CANCELLED",
            }
        )

    def native_tool_turn(self, tool_call_id: str) -> str:
        """Resolve a tool's original turn before a handler accesses a device capability.

        Args:
            tool_call_id: Pipecat function call identifier supplied to the handler.

        Returns:
            The active turn belonging to the native generation that requested the call.

        Raises:
            asyncio.CancelledError: The requesting generation is no longer active.
        """
        call = self._native_calls.get(tool_call_id)
        if (
            call is None
            or not self.bridge.active
            or call["session"] != self.bridge.session
            or call["turn"] != self.bridge.turn
            or call["request"] not in self.bridge.pending
        ):
            raise asyncio.CancelledError
        return call["turn"]

    async def _dispatch_tool_call(
        self, event: dict[str, Any], context: LLMContext, turn: str, call_ids: set[str]
    ) -> None:
        call = event["tool_call"]
        call_id, name, arguments = call.get("id"), call.get("name"), call.get("arguments")
        if not isinstance(call_id, str) or not call_id or call_id in call_ids:
            raise ValueError("Native tool call has a missing or duplicate ID")
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise ValueError("Native tool call requires a function name and argument object")
        registered = self._functions.get(name) or self._functions.get(None)
        if registered is not None and not registered.cancel_on_interruption:
            raise NotImplementedError("Native Apple tools must cancel on interruption")
        call_ids.add(call_id)
        self._native_calls[call_id] = {
            "request": event["request"],
            "session": self.bridge.session,
            "turn": turn,
        }
        await self.run_function_calls(
            [
                FunctionCallFromLLM(
                    function_name=name, tool_call_id=call_id, arguments=arguments, context=context
                )
            ]
        )

    @staticmethod
    def _validate_native_settings(settings: LLMSettings) -> None:
        unsupported = [
            key
            for key, value in settings.given_fields().items()
            if key != "system_instruction"
            and value is not None
            and not (key == "model" and value == "apple-foundation-models")
            and not (key == "filter_incomplete_user_turns" and value is False)
        ]
        if unsupported:
            raise ValueError(
                f"Native Apple settings are not configurable: {', '.join(unsupported)}"
            )

    async def _update_settings(self, delta: LLMSettings) -> dict[str, Any]:
        self._validate_native_settings(delta)
        changed = await super()._update_settings(delta)
        if self.bridge.context is not None:
            self.prepare_context(self.bridge.context)
        return changed

    def prepare_context(self, context: LLMContext) -> None:
        """Hint that native inference will soon use the committed conversation.

        Args:
            context: Context after the assistant aggregator committed played text.
        """
        if not self.bridge.active or any(
            operation == "generate" for operation, _ in self.bridge.operations.values()
        ):
            return
        try:
            params = self.get_llm_adapter().get_preparation_params(
                context, system_instruction=self._composed_system_instruction
            )
        except (ValueError, NotImplementedError):
            # Preparation is optional; the actual invocation reports bad input.
            return
        self.bridge.emit(
            {
                "type": "prewarm",
                "session": self.bridge.session,
                "turn": self.bridge.turn,
                **params,
            }
        )

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Handle context requests and preserve each generated response's turn identity.

        Args:
            frame: Incoming Pipecat frame.
            direction: Direction of travel through the pipeline.
        """
        await super().process_frame(frame, direction)
        if isinstance(
            frame, (LLMUpdateSettingsFrame, LLMConfigureOutputFrame, LLMContextSummaryRequestFrame)
        ):
            return
        if not isinstance(frame, LLMContextFrame) or direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        turn = frame.metadata.get("turn", self.bridge.turn)
        if not self.bridge.active or turn is None or turn != self.bridge.turn:
            return
        text = ""
        call_ids: set[str] = set()
        try:
            params = self.get_llm_adapter().get_llm_invocation_params(
                frame.context, system_instruction=self._composed_system_instruction
            )
            for tool in params["tools"]:
                registered = self._functions.get(tool["name"]) or self._functions.get(None)
                if registered is not None and not registered.cancel_on_interruption:
                    raise NotImplementedError("Native Apple tools must cancel on interruption")
            start = LLMFullResponseStartFrame()
            start.metadata["turn"] = turn
            await self.push_frame(start)
            async for event in self.bridge.stream("generate", turn, **params):
                if self.bridge.turn != turn or not self.bridge.active:
                    return
                if "tool_call" in event:
                    await self._dispatch_tool_call(event, frame.context, turn, call_ids)
                    continue
                delta = str(event.get("delta", ""))
                text += delta
                chunk = LLMTextFrame(delta)
                chunk.metadata["turn"] = turn
                await self.push_frame(chunk)
            if self.bridge.turn != turn or not self.bridge.active:
                return
            if not text.strip():
                raise RuntimeError("The language model returned an empty response. Try again.")
            end = LLMFullResponseEndFrame()
            end.metadata["turn"] = turn
            await self.push_frame(end)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.bridge.turn != turn or not self.bridge.active:
                return
            error = ErrorFrame(str(exc), exception=exc)
            error.metadata.update(turn=turn, operation="generate", recoverable=True)
            await self.push_error_frame(error)
        finally:
            await self._cancel_function_call_tasks(
                lambda item: item.tool_call_id in call_ids and not item.settled,
                reason="native generation ended",
            )
            self._native_calls = {
                call_id: call
                for call_id, call in self._native_calls.items()
                if call_id not in call_ids
            }
