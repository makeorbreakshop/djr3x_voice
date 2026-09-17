"""
Console logging must survive a non-blocking stdout, and must not bury the prompt.

## The defect this pins (observed live 2026-09-17 10:55:44)

    2026-09-17 10:55:44,103 - cantina_os.cli - ERROR - Error writing output:
        [Errno 35] write could not complete without blocking

Errno 35 is EAGAIN. `CLIService._setup_stdin_reader` calls
`os.set_blocking(sys.stdin.fileno(), False)`; on a terminal, stdin and stdout share one open
file description, so stdout becomes non-blocking too. Any write large enough to fill the tty
buffer then raises `BlockingIOError` - from the CLI's own writer, and from the logging
`StreamHandler`, which is pointed at `sys.stdout`.

The second half of the problem is volume: the console handler ran at INFO, and INTERACTIVE
mode emits hundreds of lines a turn, so the `DJ-R3X>` prompt was buried even when nothing
failed. Interactively the console defaults to WARNING; `LOG_LEVEL` still overrides it, and the
file handler is untouched at DEBUG so nothing is lost from the diagnostics.
"""

import io
import logging
import os
import threading
import time

import pytest

from cantina_os.core.console_logging import (
    BlockingSafeStreamHandler,
    build_console_handler,
    default_console_level,
    write_with_retry,
)


def _record(message: str = "hello") -> logging.LogRecord:
    return logging.LogRecord(
        name="cantina_os.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


class EagainThenOk(io.StringIO):
    """A stream that raises EAGAIN a fixed number of times, then behaves."""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self.remaining_failures = failures
        self.attempts = 0

    def write(self, s):  # type: ignore[override]
        self.attempts += 1
        if self.remaining_failures > 0:
            self.remaining_failures -= 1
            raise BlockingIOError(35, "write could not complete without blocking")
        return super().write(s)

    def flush(self):  # type: ignore[override]
        pass


class AlwaysEagain(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    def write(self, s):  # type: ignore[override]
        self.attempts += 1
        raise BlockingIOError(35, "write could not complete without blocking")

    def flush(self):  # type: ignore[override]
        pass


class TestBlockingSafeStreamHandler:
    def test_a_transient_eagain_does_not_lose_the_line(self):
        stream = EagainThenOk(failures=3)
        handler = BlockingSafeStreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))

        handler.emit(_record("the line that used to vanish"))

        assert "the line that used to vanish" in stream.getvalue()
        assert stream.attempts > 1, "the handler did not retry"

    def test_a_permanently_blocked_stream_does_not_raise(self):
        """A log line must never take the voice loop down with it."""
        stream = AlwaysEagain()
        handler = BlockingSafeStreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.handleError = lambda record: None  # type: ignore[assignment]

        handler.emit(_record())  # must not raise

        assert stream.attempts > 1

    def test_it_gives_up_rather_than_spinning_forever(self):
        stream = AlwaysEagain()
        handler = BlockingSafeStreamHandler(stream, max_retries=5, retry_delay_s=0.001)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.handleError = lambda record: None  # type: ignore[assignment]

        started = time.monotonic()
        handler.emit(_record())
        assert time.monotonic() - started < 1.0
        assert stream.attempts <= 6

    def test_a_real_pipe_that_returns_eagain(self):
        """The actual failure mode: a full, non-blocking pipe.

        Fill the pipe so the next write returns EAGAIN, then drain it from another thread.
        A plain StreamHandler loses the record here; this one waits and writes it.
        """
        read_fd, write_fd = os.pipe()
        os.set_blocking(write_fd, False)
        try:
            # Fill the pipe buffer.
            with pytest.raises(BlockingIOError):
                while True:
                    os.write(write_fd, b"x" * 65536)

            drained = bytearray()

            def drain():
                time.sleep(0.05)
                os.set_blocking(read_fd, False)
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    try:
                        chunk = os.read(read_fd, 65536)
                    except BlockingIOError:
                        time.sleep(0.01)
                        continue
                    if not chunk:
                        break
                    drained.extend(chunk)

            reader = threading.Thread(target=drain)
            reader.start()

            stream = os.fdopen(write_fd, "w", buffering=1)
            handler = BlockingSafeStreamHandler(stream, max_retries=200, retry_delay_s=0.01)
            handler.setFormatter(logging.Formatter("%(message)s"))
            handler.handleError = lambda record: pytest.fail("record was dropped")

            handler.emit(_record("survived the full pipe"))
            reader.join()

            assert b"survived the full pipe" in bytes(drained)
        finally:
            for fd in (read_fd,):
                try:
                    os.close(fd)
                except OSError:
                    pass


class TestDefaultConsoleLevel:
    def test_interactive_defaults_to_warning(self):
        """So `DJ-R3X>` is visible. The file handler still records everything."""
        assert default_console_level(env={}, interactive=True) == logging.WARNING

    def test_non_interactive_keeps_info(self):
        """Piped or captured output is being read by a person or a script later, not raced
        against a prompt."""
        assert default_console_level(env={}, interactive=False) == logging.INFO

    @pytest.mark.parametrize(
        "value, expected",
        [
            ("DEBUG", logging.DEBUG),
            ("info", logging.INFO),
            ("WARNING", logging.WARNING),
            ("ERROR", logging.ERROR),
        ],
    )
    def test_log_level_env_var_still_wins(self, value, expected):
        assert default_console_level(env={"LOG_LEVEL": value}, interactive=True) == expected

    def test_a_nonsense_log_level_falls_back_rather_than_raising(self):
        assert default_console_level(env={"LOG_LEVEL": "LOUD"}, interactive=True) == (
            logging.WARNING
        )


class TestBuildConsoleHandler:
    """The factory `main.py` calls, so the wiring is testable without importing `main`.

    `main.py` builds its logging at module import time, which cannot be exercised in-process
    without starting the whole system; keeping the construction here makes it assertable.
    """

    def test_it_builds_a_retrying_handler_at_the_default_level(self):
        handler = build_console_handler(stream=io.StringIO(), env={}, interactive=True)
        assert isinstance(handler, BlockingSafeStreamHandler)
        assert handler.level == logging.WARNING

    def test_log_level_still_overrides(self):
        handler = build_console_handler(
            stream=io.StringIO(), env={"LOG_LEVEL": "DEBUG"}, interactive=True
        )
        assert handler.level == logging.DEBUG

    def test_it_carries_a_formatter(self):
        handler = build_console_handler(stream=io.StringIO(), env={}, interactive=True)
        assert handler.formatter is not None


class TestWriteWithRetry:
    """The same EAGAIN problem hits `CLIService._output_processor`, which is where it was
    actually observed: "Error writing output: [Errno 35] write could not complete without
    blocking" swallowed a `help` listing on 2026-09-17 10:55:44."""

    def test_a_transient_eagain_does_not_lose_cli_output(self):
        stream = EagainThenOk(failures=3)
        assert write_with_retry(stream, "Available commands:\n") is True
        assert "Available commands:" in stream.getvalue()

    def test_it_reports_failure_rather_than_raising(self):
        stream = AlwaysEagain()
        assert write_with_retry(stream, "x\n", max_retries=3, retry_delay_s=0.001) is False
