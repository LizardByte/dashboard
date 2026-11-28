# standard imports
import json

# lib imports
import pytest

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


def test_is_bot_issue_author():
    assert helpers.is_bot_issue_author('renovate') is False
    assert helpers.is_bot_issue_author('renovate[bot]') is True
    assert helpers.is_bot_issue_author('github-actions') is False
    assert helpers.is_bot_issue_author('github-actions[bot]') is True
    assert helpers.is_bot_issue_author('LizardByte-bot') is True
    assert helpers.is_bot_issue_author('someone', 'Bot') is True
    assert helpers.is_bot_issue_author('dependabot[bot]') is True
    assert helpers.is_bot_issue_author('person') is False
    assert helpers.is_bot_issue_author(None, None) is False


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
    helpers.write_json_files(str(path1), payload, base_dir=str(tmp_path))
    assert json.loads((tmp_path / 'a' / 'file.json').read_text()) == payload

    monkeypatch.setenv('ACTIONS_STEP_DEBUG', '1')
    helpers.write_json_files(path2, payload, base_dir=tmp_path)
    content = (tmp_path / 'b' / 'file.json').read_text()
    assert '\n' in content
    assert json.loads(content) == payload


def test_write_json_files_rejects_destinations_outside_output_dir(tmp_path):
    """Reject a sibling directory before creating any output there."""
    output_dir = tmp_path / 'dashboard'
    sibling_dir = tmp_path / 'dashboard-other'

    with pytest.raises(ValueError, match='outside the dashboard output directory'):
        helpers.write_json_files(sibling_dir / 'stats', {}, base_dir=output_dir)

    assert not sibling_dir.exists()


def test_write_json_files_rejects_symlink_to_file_outside_output_dir(tmp_path):
    """Validate the resolved filename including its extension."""
    output_dir = tmp_path / 'dashboard'
    output_dir.mkdir()
    outside_file = tmp_path / 'existing.json'
    outside_file.write_text('{}', encoding='utf-8')
    try:
        (output_dir / 'stats.json').symlink_to(outside_file)
    except OSError as error:  # Windows may require symlink privileges.
        pytest.skip(f'Cannot create a symlink: {error}')

    with pytest.raises(ValueError, match='outside the dashboard output directory'):
        helpers.write_json_files(output_dir / 'stats', {'new': True}, base_dir=output_dir)

    assert outside_file.read_text(encoding='utf-8') == '{}'
