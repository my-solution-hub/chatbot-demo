"""Console logger with prefix grammar and session gating.

This module is named ``logging`` to mirror the conceptual role described in
the design document. To avoid shadowing the standard library ``logging``
module from inside this file, we use absolute imports only and never write
``import logging`` here.

Output rules (Requirements 5.1-5.8):

* ``banner`` and ``listening`` always emit, regardless of session state.
* ``user``, ``assistant``, ``tool_call``, and ``tool_result`` emit only when
  the session is marked active. Otherwise they are no-ops (no buffering).
* All emissions go to ``sys.stdout`` and end with ``\\n``. ``sys.stdout`` is
  re-resolved on every call so ``capsys`` and other test fixtures that swap
  the stream out can capture the output.
* ``tool_call`` and ``tool_result`` serialize their JSON payload on a single
  line. If the payload cannot be serialized, the entire payload is replaced
  by the literal string ``<non-serializable>`` (without surrounding quotes).
"""

from __future__ import annotations

import json
import sys
from typing import Any


_NON_SERIALIZABLE = "<non-serializable>"


def _serialize_payload(value: Any) -> str:
    """Return ``value`` as single-line JSON or ``<non-serializable>``.

    The serializer is intentionally strict: if ``json.dumps`` cannot encode
    the value as-is, the *entire* payload is substituted, not just the
    offending sub-tree. This matches the contract documented in the design
    and in Requirement 5.8.
    """
    try:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError, RecursionError):
        return _NON_SERIALIZABLE


class ConsoleLogger:
    """Stdout writer that enforces the demo's prefix grammar.

    A single instance is shared across the demo. The session lifecycle calls
    :meth:`mark_session_active` after the Bedrock stream is fully open and
    :meth:`mark_session_closed` during shutdown so that pre- and post-session
    transcript / tool events are dropped at emission time.
    """

    def __init__(self) -> None:
        self._session_active = False

    # ------------------------------------------------------------------
    # Session gating
    # ------------------------------------------------------------------

    def mark_session_active(self) -> None:
        """Enable user/assistant/tool log lines. Idempotent."""
        self._session_active = True

    def mark_session_closed(self) -> None:
        """Disable user/assistant/tool log lines. Idempotent."""
        self._session_active = False

    @property
    def is_session_active(self) -> bool:
        return self._session_active

    # ------------------------------------------------------------------
    # Always-on emissions
    # ------------------------------------------------------------------

    def banner(self, model_id: str, region: str) -> None:
        """Print the startup banner. Always emits, regardless of session state."""
        self._write(f"Nova Sonic Demo: model={model_id} region={region}\n")

    def listening(self) -> None:
        """Print the LISTENING line. Always emits, regardless of session state."""
        self._write("LISTENING: ready for speech\n")

    # ------------------------------------------------------------------
    # Session-gated emissions
    # ------------------------------------------------------------------

    def user(self, text: str) -> None:
        if not self._session_active:
            return
        self._write(f"USER: {text}\n")

    def assistant(self, text: str) -> None:
        if not self._session_active:
            return
        self._write(f"ASSISTANT: {text}\n")

    def tool_call(self, name: str, arguments: Any) -> None:
        if not self._session_active:
            return
        payload = _serialize_payload(arguments)
        self._write(f"TOOL_CALL: {name} {payload}\n")

    def tool_result(self, name: str, result: Any) -> None:
        if not self._session_active:
            return
        payload = _serialize_payload(result)
        self._write(f"TOOL_RESULT: {name} {payload}\n")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _write(line: str) -> None:
        # Re-resolve sys.stdout on every call so test fixtures that swap the
        # stream (capsys, monkeypatch, etc.) can observe the writes. After a
        # write we flush so audience members and tests see output promptly.
        stream = sys.stdout
        stream.write(line)
        try:
            stream.flush()
        except Exception:
            # A flush failure must never propagate. Logger output is best
            # effort and the session must remain healthy (Requirement 5.8 /
            # property P4).
            pass
