import logging, os, sys
from logging import Logger
from logging.handlers import RotatingFileHandler


def get_logger(
    name: str,
    level: int | str = logging.INFO,
    log_dir: str = "logs",
    file_name: str = "api.log",
) -> Logger:
    """Create a universal logger object that dumps logs to a given directory.
    Created in part with Claude.

    Args:
        name (str): The name of the logger
        level (int | str, optional): The log level enum value. Defaults to logging.INFO.
        log_dir (str, optional): The directory the logs will be stored in. Defaults to "logs".
        file_name (str, optional): The file name the logs will be stored in. Defaults to "api.log".

    Returns:
        Logger: The custom logger object that dumps the logs to your defined location
    """

    logger = logging.getLogger(name)

    # Avoid adding duplicate handlers
    if logger.handlers:
        return logger

    # The logger level based on the enum input
    logger.setLevel(level)

    # Add a formatter for the log output
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )

    # Optionally add a rotating file handler
    if not os.path.exists(os.path.join(log_dir, file_name)):
        os.makedirs(os.path.dirname(os.path.join(log_dir, file_name)), exist_ok=True)

    file_handler = RotatingFileHandler(
        os.path.join(log_dir, file_name),
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Always add a stdout handler
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    logger.addHandler(stdout_handler)

    # Prevent log messages from propagating to the root logger
    logger.propagate = False

    return logger
