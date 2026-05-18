"""Entry point for ``python -m nova_sonic_demo.web``.

Starts the FastAPI web server using uvicorn.

Requirements: 1.1
"""

from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    """Parse CLI arguments and start the uvicorn server."""
    parser = argparse.ArgumentParser(
        description="Start the Nova Sonic web UI server",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host address to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to listen on (default: 8000)",
    )
    args = parser.parse_args()

    uvicorn.run(
        "nova_sonic_demo.web.app:app",
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
