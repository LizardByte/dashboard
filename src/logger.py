# standard imports
import logging
import os
import pathlib

# get the base directory from the existing project structure
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def setup_logger(name: str = __name__) -> logging.Logger:
    """
    Configure and return a logger that writes only to a file.

    Parameters
    ----------
    name : str
        The name for the logger instance

    Returns
    -------
    logging.Logger
        Configured logger instance
    """
    # Create logs directory if it doesn't exist
    log_dir = os.path.join(BASE_DIR, 'logs')
    pathlib.Path(log_dir).mkdir(parents=True, exist_ok=True)

    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # Create file handler
    handler = logging.FileHandler(
        filename=os.path.join(log_dir, 'updater.log'),
        encoding='utf-8',
        mode='w'  # Overwrite file each run
    )

    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    handler.setFormatter(formatter)

    # Add handler to logger
    logger.addHandler(handler)

    # Prevent propagation to root logger (stops console output)
    logger.propagate = False

    return logger


# Create default logger instance
log = setup_logger()
