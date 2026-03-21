# standard imports
import json

# local imports
from src import helpers


def test_timeout_session_sets_default_timeout(monkeypatch):
    called = {}

    def fake_request(self, *args, **kwargs):
        called['kwargs'] = kwargs
        return 'ok'

    monkeypatch.setattr('requests.Session.request', fake_request)

    session = helpers.TimeoutSession()
    result = session.request('GET', 'https://example.com')

    assert result == 'ok'
    assert called['kwargs']['timeout'] == helpers.DEFAULT_TIMEOUT


def test_timeout_session_keeps_explicit_timeout(monkeypatch):
    called = {}

    def fake_request(self, *args, **kwargs):
        called['kwargs'] = kwargs
        return 'ok'

    monkeypatch.setattr('requests.Session.request', fake_request)

    session = helpers.TimeoutSession()
    session.request('GET', 'https://example.com', timeout=5)

    assert called['kwargs']['timeout'] == 5


def test_rate_limited_session_waits_when_called_too_fast(monkeypatch):
    timeline = iter([100.0, 100.1])
    slept = []

    monkeypatch.setattr('time.time', lambda: next(timeline))
    monkeypatch.setattr('time.sleep', lambda secs: slept.append(round(secs, 3)))
    monkeypatch.setattr('requests.Session.request', lambda *args, **kwargs: 'ok')

    session = helpers.RateLimitedSession(calls_per_minute=60)
    session.last_call_time = 99.5
    session.request('GET', 'https://example.com')

    assert slept == [0.5]


def test_rate_limited_session_no_wait_when_interval_elapsed(monkeypatch):
    timeline = iter([100.0, 102.0, 102.0])

    def fail_sleep(_secs):
        raise AssertionError('must not sleep')

    monkeypatch.setattr('time.time', lambda: next(timeline))
    monkeypatch.setattr('time.sleep', fail_sleep)
    monkeypatch.setattr('requests.Session.request', lambda *args, **kwargs: 'ok')

    session = helpers.RateLimitedSession(calls_per_minute=60)
    assert session.request('GET', 'https://example.com') == 'ok'


def test_debug_print_logs_and_prints(monkeypatch):
    logs = []
    prints = []

    monkeypatch.setattr(helpers.log, 'debug', lambda msg: logs.append(msg))
    monkeypatch.setattr('builtins.print', lambda *args, **kwargs: prints.append((args, kwargs)))
    monkeypatch.setenv('ACTIONS_RUNNER_DEBUG', '1')

    helpers.debug_print('a', 'b', sep='|', end='!')

    assert logs == ['a|b']
    assert prints
    first_args, _first_kwargs = prints[0]
    assert first_args == ('a', 'b')


def test_debug_print_no_stdout_without_debug_flags(monkeypatch):
    logs = []

    monkeypatch.setattr(helpers.log, 'debug', lambda msg: logs.append(msg))
    monkeypatch.delenv('ACTIONS_RUNNER_DEBUG', raising=False)
    monkeypatch.delenv('ACTIONS_STEP_DEBUG', raising=False)

    def fail_print(*_args, **_kwargs):
        raise AssertionError('unexpected print')

    monkeypatch.setattr('builtins.print', fail_print)

    helpers.debug_print('hello')

    assert logs == ['hello']


def test_save_image_from_url_writes_original_and_resized(monkeypatch, tmp_path):
    out = tmp_path / 'images' / 'graph'

    monkeypatch.setattr(helpers, 'debug_print', lambda *args, **kwargs: None)

    class FakeImage:
        def __init__(self):
            self.saved = []

        def resize(self, shape):
            self.shape = shape
            return self

        def save(self, fp):
            self.saved.append(fp)

    fake = FakeImage()
    monkeypatch.setattr(helpers.s, 'get', lambda url: type('R', (), {'content': b'data'})())
    monkeypatch.setattr('PIL.Image.open', lambda path: fake)

    helpers.save_image_from_url(str(out), 'png', 'https://example.com/a.png', size_x=100, size_y=50)

    assert (tmp_path / 'images' / 'graph.png').read_bytes() == b'data'
    assert fake.shape == (100, 50)
    assert fake.saved == [str(out) + '_100x50.png']


def test_write_json_files_with_and_without_indent(monkeypatch, tmp_path):
    path1 = tmp_path / 'a' / 'file'
    path2 = tmp_path / 'b' / 'file'
    payload = {'x': 1}

    monkeypatch.setattr(helpers, 'debug_print', lambda *args, **kwargs: None)

    monkeypatch.delenv('ACTIONS_RUNNER_DEBUG', raising=False)
    monkeypatch.delenv('ACTIONS_STEP_DEBUG', raising=False)
    helpers.write_json_files(str(path1), payload)
    assert json.loads((tmp_path / 'a' / 'file.json').read_text()) == payload

    monkeypatch.setenv('ACTIONS_STEP_DEBUG', '1')
    helpers.write_json_files(str(path2), payload)
    content = (tmp_path / 'b' / 'file.json').read_text()
    assert '\n' in content
    assert json.loads(content) == payload
