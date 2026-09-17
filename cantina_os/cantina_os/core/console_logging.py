"""
Console logging that survives a non-blocking stdout, at a level that leaves the prompt visible.

## Why this module exists

``CLIService._setup_stdin_reader`` calls ``os.set_blocking(sys.stdin.fileno(), False)`` so
``loop.connect_read_pipe`` can drive stdin. On a terminal, stdin and stdout are two file
descriptors onto the **same open file description**, and O_NONBLOCK lives on the description -
so making stdin non-blocking makes stdout non-blocking too. Once the tty buffer fills, a write
returns EAGAIN, which Python raises as ``BlockingIOError``. Observed live 2026-09-17 10:55:44:

    cantina_os.cli - ERROR - Error writing output:
        [Errno 35] write could not complete without blocking

Setting stdout blocking again is not available as a fix: it is the same description, so it
would put stdin back into blocking mode and break the reader. Retrying the write is, and it is
also the correct behaviour for a logging handler - the line is not unwriteable, just not
writeable *yet*.

The volume half of the problem is separate: the console handler ran at INFO while INTERACTIVE
mode emits hundreds of lines per turn, so ``DJ-R3X>`` was buried even when nothing failed.
"""

import logging
import os
import time
from typing import Mapping, Optional

#: Total patience for one log line: 100 attempts x 10 ms. A tty that cannot accept a line in
#: a second is not going to, and a log line must never hold the process longer than that.
DEFAULT_MAX_RETRIES = 100
DEFAULT_RETRY_DELAY_S = 0.01


def write_with_retry(
    stream,
    text: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_delay_s: float = DEFAULT_RETRY_DELAY_S,
) -> bool:
    """Write ``text`` to ``stream``, waiting out EAGAIN.

    Same reasoning as :class:`BlockingSafeStreamHandler`, for the plain writes
    ``CLIService._output_processor`` does. Returns True if the text was written.
    """
    for attempt in range(max_retries + 1):
        try:
            stream.write(text)
            stream.flush()
            return True
        except BlockingIOError:
            if attempt >= max_retries:
                return False
            time.sleep(retry_delay_s)
    return False


class BlockingSafeStreamHandler(logging.StreamHandler):
    """A ``StreamHandler`` that waits out EAGAIN instead of dropping the record.

    ``logging.StreamHandler.emit`` routes every exception to ``handleError``, which by default
    prints to stderr and discards the record. For ``BlockingIOError`` that is the wrong call:
    the stream is momentarily full, not broken. Retry briefly, then give up the normal way.
    """

    def __init__(
        self,
        stream=None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay_s: float = DEFAULT_RETRY_DELAY_S,
    ) -> None:
        super().__init__(stream)
        self._max_retries = max_retries
        self._retry_delay_s = retry_delay_s

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102 - inherited contract
        try:
            message = self.format(record)
        except Exception:  # noqa: BLE001 - formatting failures use the standard path
            self.handleError(record)
            return

        terminator = self.terminator
        for attempt in range(self._max_retries + 1):
            try:
                self.stream.write(message + terminator)
                self.flush()
                return
            except BlockingIOError:
                if attempt >= self._max_retries:
                    break
                time.sleep(self._retry_delay_s)
            except Exception:  # noqa: BLE001 - anything else is a real handler error
                self.handleError(record)
                return

        # Still blocked after the full retry budget. Report it the standard way rather than
        # raising into whatever happened to be logging.
        self.handleError(record)


def default_console_level(
    env: Optional[Mapping[str, str]] = None, interactive: Optional[bool] = None
) -> int:
    """The level the console handler should start at.

    Args:
        env: Environment to read ``LOG_LEVEL`` from. Defaults to ``os.environ``.
        interactive: Whether a person is watching a terminal prompt. Defaults to whether
            stdout is a tty.

    Returns:
        A ``logging`` level. ``LOG_LEVEL`` always wins, so nothing about diagnosing a problem
        changes. Otherwise WARNING when interactive - the CLI prompt has to be visible, and the
        file handler is recording everything at DEBUG regardless - and INFO when output is
        being piped or captured, where there is no prompt to race against.
    """
    if env is None:
        env = os.environ

    requested = (env.get("LOG_LEVEL") or "").strip().upper()
    if requested:
        level = logging.getLevelName(requested)
        if isinstance(level, int):
            return level
        # An unparseable LOG_LEVEL is a typo, not an instruction to go quiet or loud.

    if interactive is None:
        try:
            import sys

            interactive = bool(sys.stdout.isatty())
        except Exception:  # noqa: BLE001
            interactive = False

    return logging.WARNING if interactive else logging.INFO


def build_console_handler(
    stream=None,
    formatter: Optional[logging.Formatter] = None,
    env: Optional[Mapping[str, str]] = None,
    interactive: Optional[bool] = None,
) -> BlockingSafeStreamHandler:
    """The console handler ``main.py`` installs.

    Constructed here rather than inline in ``main.py`` so it can be asserted on without
    importing the whole system - ``main.py`` builds its logging at module import time, before
    any service exists, which is correct but untestable in-process.

    Args:
        stream: Target stream. Defaults to ``sys.stdout``.
        formatter: Formatter to use. Defaults to the minimal CLI formatter, falling back to a
            plain one if it cannot be built.
        env: Environment to read ``LOG_LEVEL`` from.
        interactive: Whether a person is watching a prompt.
    """
    import sys

    handler = BlockingSafeStreamHandler(stream if stream is not None else sys.stdout)

    if formatter is None:
        try:
            from ..utils.cli_formatter_minimal import setup_minimal_logging_formatter

            formatter = setup_minimal_logging_formatter()
        except Exception:  # noqa: BLE001 - a missing formatter must not stop logging
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
    handler.setFormatter(formatter)

    handler.setLevel(default_console_level(env=env, interactive=interactive))
    return handler
