"""Nova Sonic Demo — Web UI module.

Provides a FastAPI-based web server with WebSocket support for
browser-based voice sessions.

Requirements: 1.1, 1.2
"""

from nova_sonic_demo.web.app import app
from nova_sonic_demo.web.session_manager import SessionManager

__all__ = ["app", "SessionManager"]
