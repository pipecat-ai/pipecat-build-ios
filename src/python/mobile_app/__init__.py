"""Embedded entry point. Native audio never crosses the Python boundary."""


def run():
    import asyncio
    import json

    import _pipecat_native
    from loguru import logger

    from .bot import VoiceAgent

    logger.remove()
    logger.add(lambda message: _pipecat_native.log(str(message)), level="WARNING")

    async def main():
        agent = VoiceAgent(
            lambda event: _pipecat_native.emit(json.dumps(event)),
            sentence_boundary_matcher=_pipecat_native.sentence_boundary,
        )
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(agent.run())
            await agent.ready.wait()
            while True:
                raw = _pipecat_native.poll()
                if raw is None:
                    await asyncio.sleep(0.015)
                    continue
                try:
                    event = json.loads(raw)
                    if event.get("type") == "shutdown":
                        await agent.close()
                        break
                    await agent.receive(event)
                except Exception as exc:
                    agent.emit({"type": "error", "message": str(exc)})

    asyncio.run(main())
