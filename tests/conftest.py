# standard imports
import io
import json
from types import SimpleNamespace

# lib imports
import pytest


class DummyResponse:
    def __init__(self, status_code=200, payload=None, text='err', content=b'img'):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text
        self.content = content

    def json(self):
        return self._payload


@pytest.fixture
def dummy_response_class():
    return DummyResponse


@pytest.fixture
def open_json_reader(monkeypatch):
    def _apply(path, payload):
        original_open = open

        def _open(file, mode='r', *args, **kwargs):
            if str(file) == str(path) and 'r' in mode:
                return io.StringIO(json.dumps(payload))
            return original_open(file, mode, *args, **kwargs)

        monkeypatch.setattr('builtins.open', _open)

    return _apply


@pytest.fixture
def fake_repo():
    owner = SimpleNamespace(login='LizardByte')
    repo = SimpleNamespace(
        name='demo',
        owner=owner,
        archived=False,
        raw_data={'name': 'demo'},
        stargazers_count=10,
    )
    return repo
