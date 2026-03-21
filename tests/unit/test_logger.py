# standard imports
import logging

# local imports
from src import logger


def test_setup_logger_creates_log_file(monkeypatch, tmp_path):
    monkeypatch.setattr(logger, 'BASE_DIR', str(tmp_path))

    log1 = logger.setup_logger('unit.logger')
    log1.info('hello')

    log_file = tmp_path / 'logs' / 'updater.log'
    assert log_file.exists()
    assert 'hello' in log_file.read_text(encoding='utf-8')
    assert log1.level == logging.INFO
    assert log1.propagate is False


def test_setup_logger_adds_handler_each_call(monkeypatch, tmp_path):
    monkeypatch.setattr(logger, 'BASE_DIR', str(tmp_path))

    name = 'unit.logger.handlers'
    lg = logging.getLogger(name)
    for handler in tuple(lg.handlers):
        lg.removeHandler(handler)

    first = logger.setup_logger(name)
    second = logger.setup_logger(name)

    assert first is second
    assert len(second.handlers) == 2
