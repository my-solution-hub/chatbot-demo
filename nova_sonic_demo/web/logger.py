"""WebLogger — routes tool activity to a WebSocket instead of stdout.

Subclasses :class:`~nova_sonic_demo.logging.ConsoleLogger` and overrides
``tool_call`` / ``tool_result`` to serialize events as JSON dicts and
deliver them via an async callback (typically bound to a WebSocket send).

The inherited ``_session_active`` flag gates emissions: when the session is
not active, tool events are silently suppressed (no buffering, no sending).

Non-serializable payloads are replaced with the literal string
``"<non-serializable>"`` to match the contract defined in Requirement 8.5.

Requirements: 8.1, 8.2, 8.3, 8.4, 8.5
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from nova_sonic_demo.logging import ConsoleLogger


_NON_SERIALIZABLE = "<non-serializable>"


def _safe_payload(value: Any) -> Any:
    """Return *value* if JSON-serializable, else the sentinel string.

    We attempt a round-trip through ``json.dumps`` to verify serializability.
    If that fails, the *entire* payload is substituted — not just the
    offending sub-tree.
    """
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError, RecursionError):
        return _NON_SERIALIZABLE


class WebLogger(ConsoleLogger):
    """Logger that emits tool events as dicts via an async send function.

    Parameters
    ----------
    send_fn:
        An async callable that accepts a single ``dict`` argument and
        delivers it to the connected WebSocket client.
    """

    def __init__(self, send_fn: Callable[[dict], Awaitable[None]]) -> None:
        super().__init__()
        self._send_fn = send_fn

    # ------------------------------------------------------------------
    # Overrides — suppress stdout
    # ------------------------------------------------------------------

    @staticmethod
    def _write(line: str) -> None:  # noqa: D102
        """No-op: suppress all stdout output."""

    # ------------------------------------------------------------------
    # Overrides — route tool events to WebSocket
    # ------------------------------------------------------------------

    def tool_call(self, name: str, arguments: Any) -> None:
        """Serialize and send a tool_call event via the WebSocket callback.

        Respects session-active gating: if the session is not active the
        call is a no-op (Requirement 8.4).
        """
        if not self._session_active:
            return
        payload = _safe_payload(arguments)
        # Fire-and-forget: the coroutine is returned to the caller's event
        # loop via the send_fn contract.  We intentionally do NOT await here
        # because ConsoleLogger.tool_call is synchronous.  The SessionManager
        # is responsible for scheduling the coroutine.
        import asyncio

        asyncio.ensure_future(
            self._send_fn({"type": "tool_call", "name": name, "arguments": payload})
        )

    def tool_result(self, name: str, result: Any) -> None:
        """Serialize and send a tool_result event via the WebSocket callback.

        Respects session-active gating: if the session is not active the
        call is a no-op (Requirement 8.4).
        """
        if not self._session_active:
            return
        payload = _safe_payload(result)
        import asyncio

        asyncio.ensure_future(
            self._send_fn({"type": "tool_result", "name": name, "result": payload})
        )
