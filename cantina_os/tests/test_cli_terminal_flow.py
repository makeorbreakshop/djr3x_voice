import asyncio
import io

import pytest
from cantina_os.services.cli_service import CLIService
from cantina_os.services.command_dispatcher_service import CommandDispatcherService
from pyee.asyncio import AsyncIOEventEmitter


class QueueReader:
    def __init__(self) -> None:
        self.lines: asyncio.Queue[bytes] = asyncio.Queue()

    async def readline(self) -> bytes:
        return await self.lines.get()


async def wait_for_text(stream: io.StringIO, needle: str) -> None:
    for _ in range(100):
        if needle in stream.getvalue():
            return
        await asyncio.sleep(0.01)
    pytest.fail(f"timed out waiting for {needle!r}; output was {stream.getvalue()!r}")


@pytest.mark.asyncio
async def test_help_renders_once_and_returns_one_prompt(monkeypatch) -> None:
    """Drive the same CLI -> dispatcher -> CLI response path a person uses."""
    output = io.StringIO()

    bus = AsyncIOEventEmitter()
    dispatcher = CommandDispatcherService(bus)
    cli = CLIService(bus)
    cli._output_stream = output
    cli._error_stream = output
    reader = QueueReader()

    async def use_queue_reader() -> None:
        cli._stdin_reader = reader

    monkeypatch.setattr(cli, "_setup_stdin_reader", use_queue_reader)

    await dispatcher.start()
    await cli.start()
    try:
        await reader.lines.put(b"help\n")
        await wait_for_text(output, "Available commands:")
        await cli._output_queue.join()

        rendered = output.getvalue()
        assert rendered.count("Available commands:") == 1
        assert rendered.count("dj-r3x>") == 2, (
            "expected the initial prompt and one prompt after help"
        )
        assert rendered.rstrip().endswith("dj-r3x>")
    finally:
        await cli.stop()
        await dispatcher.stop()
