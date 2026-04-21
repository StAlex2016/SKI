import logging
import os
from logging.handlers import RotatingFileHandler

LOG_DIR = os.getenv("LOG_DIR", "logs")
LOG_FILE = os.path.join(LOG_DIR, "bot.log")

os.makedirs(LOG_DIR, exist_ok=True)


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("ski_bot")

    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s"
    )

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=10_000_000,   # 10 MB per file
        backupCount=10,         # keep 10 rotated files → ~100 MB total
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


logger = setup_logger()


def log_event(event_name: str, **kwargs):
    details = " | ".join(f"{k}={v}" for k, v in kwargs.items())
    logger.info(f"{event_name} | {details}")