from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import MachineConfigStore


def configure_logging(log_dir: Path | None = None) -> logging.Logger:
    directory = log_dir or MachineConfigStore.default_path().parent / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("icp_renov_contracts")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(isinstance(handler, RotatingFileHandler) for handler in logger.handlers):
        handler = RotatingFileHandler(
            directory / "application.log",
            maxBytes=512 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger

