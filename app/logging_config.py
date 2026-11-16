"""Console + rotating file logging."""
import logging
import os
from logging.handlers import RotatingFileHandler

from .config import settings


def setup_logging() -> None:
    os.makedirs(settings.log_dir, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    root = logging.getLogger()
    if root.handlers:  # avoid duplicate handlers on reload
        return
    root.setLevel(settings.log_level)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    file = RotatingFileHandler(
        os.path.join(settings.log_dir, "app.log"), maxBytes=1_000_000, backupCount=3
    )
    file.setFormatter(fmt)
    root.addHandler(console)
    root.addHandler(file)
