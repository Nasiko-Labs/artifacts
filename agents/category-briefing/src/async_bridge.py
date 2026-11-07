"""Run an async gateway call from CrewAI's synchronous tool code.

CrewAI tools are sync and `Crew.kickoff_async` runs the crew in a worker thread
(`asyncio.to_thread`) while the A2A executor's event loop keeps serving. The gateway client is an
`httpx.AsyncClient` bound to that loop, so the call is scheduled back onto it with
`run_coroutine_threadsafe` and the worker thread waits. `asyncio.run` inside a tool would start a
second loop (and fails outright under a running one); blocking on the loop's own thread would
deadlock, so that is refused.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")
DEFAULT_TIMEOUT_SECS = 180.0


class LoopBridge:
    def __init__(self, loop: asyncio.AbstractEventLoop, timeout_secs: float = DEFAULT_TIMEOUT_SECS):
        self._loop = loop
        self._timeout_secs = timeout_secs
        self._loop_thread_id = threading.get_ident() if _running_here(loop) else None

    def run(self, make_coroutine: Callable[[], Awaitable[T]]) -> T:
        """Run `make_coroutine()` on the bridged loop and wait for its result."""
        if self._loop.is_closed() or not self._loop.is_running():
            raise RuntimeError("the A2A event loop is not running; cannot reach the gateway")
        if _running_here(self._loop) or threading.get_ident() == self._loop_thread_id:
            raise RuntimeError(
                "a sync tool ran on the event loop thread; run the crew with kickoff_async "
                "so tools execute in a worker thread"
            )
        future = asyncio.run_coroutine_threadsafe(_as_coroutine(make_coroutine), self._loop)
        try:
            return future.result(timeout=self._timeout_secs)
        except TimeoutError:
            future.cancel()
            raise


def _running_here(loop: asyncio.AbstractEventLoop) -> bool:
    try:
        return asyncio.get_running_loop() is loop
    except RuntimeError:
        return False


async def _as_coroutine(make_coroutine: Callable[[], Awaitable[T]]) -> T:
    return await make_coroutine()
