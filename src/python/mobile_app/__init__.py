"""Embedded entry point. Native audio never crosses the Python boundary."""


def run():
    import asyncio
    import json

    import _pipecat_native
    from loguru import logger
    from pipecat.services.apple.bridge import AppleNativeBridge
    from pipecat.workers.runner import WorkerRunner

    from .bot import create_bot

    logger.remove()
    logger.add(lambda message: _pipecat_native.log(str(message)), level="WARNING")

    async def main():
        bridge = AppleNativeBridge(lambda event: _pipecat_native.emit(json.dumps(event)))
        worker = create_bot(bridge, sentence_boundary_matcher=_pipecat_native.sentence_boundary)
        runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
        await runner.add_workers(worker)
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(runner.run())
            await bridge.ready.wait()
            while True:
                raw = _pipecat_native.poll()
                if raw is None:
                    await asyncio.sleep(0.015)
                    continue
                try:
                    event = json.loads(raw)
                    if event.get("type") == "shutdown":
                        await runner.cancel()
                        break
                    await bridge.receive(event)
                except Exception as exc:
                    bridge.emit({"type": "error", "message": str(exc)})

    asyncio.run(main())
