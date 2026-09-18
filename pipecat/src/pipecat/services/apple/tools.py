"""Await native device capabilities from ordinary Pipecat function handlers."""

from typing import Any

from pipecat.services.apple.bridge import AppleNativeBridge


class AppleDeviceTools:
    """Expose read-only native capabilities through the active conversation bridge."""

    def __init__(self, bridge: AppleNativeBridge) -> None:
        """Initialize the native client.

        Args:
            bridge: Bridge shared by the native Apple services.
        """
        self._bridge = bridge

    async def calendar_events(self, *, tool_call_id: str, days: int = 1) -> dict[str, Any]:
        """Read events from today through a bounded number of local calendar days.

        Args:
            tool_call_id: Pipecat call identifier used to reject stale invocations.
            days: Number of days starting at local midnight today, from one to seven.

        Returns:
            Native calendar access status, time window, and at most five events.
        """
        if type(days) is not int or not 1 <= days <= 7:
            return {"status": "invalid_arguments", "message": "days must be an integer from 1 to 7"}
        if self._bridge.llm is None:
            raise RuntimeError("Calendar access requires an active conversation turn")
        turn = self._bridge.llm.native_tool_turn(tool_call_id)
        result = None
        async for event in self._bridge.stream("calendar_events", turn, days=days):
            if isinstance(event.get("value"), dict):
                result = event["value"]
        if result is None:
            raise RuntimeError("The native calendar operation returned no result")
        return result
