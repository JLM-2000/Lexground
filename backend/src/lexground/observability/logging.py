from __future__ import annotations

import logging
import sys

from pythonjsonlogger.json import JsonFormatter

_CONFIGURED = False


def configure_logging(level: int = logging.INFO) -> None:
    """JSON to stdout — the container runtime owns collection and rotation."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            rename_fields={"asctime": "timestamp", "levelname": "level"},
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    logging.getLogger("uvicorn.access").handlers.clear()
    _CONFIGURED = True
