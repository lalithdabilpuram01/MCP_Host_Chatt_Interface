import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from backend.config import Settings


LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(settings: Settings) -> Path:
    """Configure console and rotating file logs for the backend process."""
    log_dir = Path(settings.log_dir)
    if not log_dir.is_absolute():
        log_dir = Path(__file__).resolve().parents[1] / log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / settings.log_file

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        logging.getLogger(logger_name).setLevel(level)

    logging.getLogger(__name__).info("Logging configured. log_file=%s level=%s", log_path, settings.log_level)
    return log_path
