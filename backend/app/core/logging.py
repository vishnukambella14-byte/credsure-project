"""Basic application logging configuration."""

import logging

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging once; safe to call repeatedly."""
    root = logging.getLogger()
    if not any(getattr(h, "_credsure", False) for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        handler._credsure = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.setLevel(level.upper())
