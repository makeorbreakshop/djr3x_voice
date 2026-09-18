"""
Console logging that survives a non-blocking stdout, at a level that leaves the prompt visible.

## Why this module exists

The original CLI connected asyncio directly to ``sys.stdin``. asyncio makes a read pipe
non-blocking; on this terminal, that also left stdout able to raise EAGAIN once the tty buffer
filled. Observed live 2026-09-17 10:55:44:

    cantina_os.cli - ERROR - Error writing output:
        [Errno 35] write could not complete without blocking

The CLI now reopens the concrete terminal device as a separate file description for
asynchronous input, which keeps normal stdout blocking. This writer remains defensive for
redirected/non-blocking streams and for transient flush failures: a line is not unwriteable
just because it is not writeable *yet*.
Most importantly, accepted text is never submitted again merely because ``flush`` hit EAGAIN;
that was the direct cause of the repeated ``help`` listings.

The CLI now owns prompt ordering, so normal INFO-level service startup remains visible without
reintroducing duplicated command output.
"""

import logging
import os
import time
from collections.abc import Mapping
from io import UnsupportedOperation
from typing import Optional

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
    """Write ``text`` to ``stream`` exactly once, waiting out EAGAIN.

    Same reasoning as :class:`BlockingSafeStreamHandler`, for the plain writes
    ``CLIService._output_processor`` does. File-descriptor streams use ``os.write`` so a
    partial write can resume at the exact byte offset. File-like test streams keep write and
    flush retries separate: once ``write`` accepts the text, a flush-time EAGAIN must never
    cause the text to be submitted again.

    Returns True if the entire text was written and flushed.
    """
    try:
        fd = stream.fileno()
    except (AttributeError, OSError, UnsupportedOperation):
        fd = None

    if fd is not None and not os.get_blocking(fd):
        encoding = getattr(stream, "encoding", None) or "utf-8"
        data = text.encode(encoding, errors="replace")
        offset = 0
        retries = 0

        while offset < len(data):
            try:
                written = os.write(fd, data[offset:])
                if written <= 0:
                    return False
                offset += written
                retries = 0
            except BlockingIOError:
                if retries >= max_retries:
                    return False
                retries += 1
                time.sleep(retry_delay_s)
        return True

    write_accepted = False
    for attempt in range(max_retries + 1):
        try:
            written = stream.write(text)
            write_accepted = written is None or written == len(text)
            if write_accepted:
                break
            return False
        except BlockingIOError:
            if attempt >= max_retries:
                return False
            time.sleep(retry_delay_s)

    if not write_accepted:
        return False

    for attempt in range(max_retries + 1):
        try:
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

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:  # noqa: BLE001 - formatting failures use the standard path
            self.handleError(record)
            return

        try:
            if write_with_retry(
                self.stream,
                message + self.terminator,
                max_retries=self._max_retries,
                retry_delay_s=self._retry_delay_s,
            ):
                return
        except Exception:  # noqa: BLE001 - real handler errors use the standard path
            self.handleError(record)
            return

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
        A ``logging`` level. ``LOG_LEVEL`` always wins. Otherwise INFO keeps startup progress
        and service readiness visible in both interactive and captured output.
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

    return logging.INFO


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
